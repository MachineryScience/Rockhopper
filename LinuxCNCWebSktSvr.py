#!/usr/bin/python 
# -*- coding: cp1252 -*-

# *****************************************************
# *****************************************************
# WebServer Interface for LinuxCNC system
#
# Usage: LinuxCNCWebSktSvr.py <LinuxCNC_INI_file_name>
#
# Provides a web server using normal HTTP/HTTPS communication
# to information about the running LinuxCNC system.  Most
# data is transferred to and from the server over a
# WebSocket using JSON formatted commands and replies.
#
#
# ***************************************************** 
# *****************************************************
#
# Copyright 2012, 2013 Machinery Science, LLC
#
import sys
import os
import linuxcnc
import math
import tornado.ioloop
import tornado.web
import tornado.autoreload
import tornado.websocket
import logging
import json
import subprocess
import hal
import time
import MakeHALGraph
from copy import deepcopy
import re
import ssl
import GCodeReader
from ConfigParser import SafeConfigParser
import hashlib
import base64
#import rpdb2
import socket
import time
import threading
import fcntl
import signal
import select
import glob
from random import random
from time import strftime
from optparse import OptionParser
    
UpdateStatusPollPeriodInMilliSeconds = 50
UpdateHALPollPeriodInMilliSeconds = 500
UpdateErrorPollPeriodInMilliseconds = 25

eps = float(0.000001)

main_loop =tornado.ioloop.IOLoop.instance()

linuxcnc_command = linuxcnc.command()

INI_FILENAME = ''
INI_FILE_PATH = ''

CONFIG_FILENAME = 'CLIENT_CONFIG.JSON'

MAX_BACKPLOT_LINES=50000

instance_number = 0

lastLCNCerror = ""

options = ""

lastBackplotFilename = ""
lastBackplotData = ""
BackplotLock = threading.Lock() 


# *****************************************************
# Class to poll linuxcnc for status.  Other classes can request to be notified
# when a poll happens with the add/del_observer methods
# *****************************************************
class LinuxCNCStatusPoller(object):
    def __init__(self, main_loop, UpdateStatusPollPeriodInMilliSeconds):
        global lastLCNCerror
        # open communications with linuxcnc
        self.linuxcnc_status = linuxcnc.stat()
        try:
            self.linuxcnc_status.poll()
            self.linuxcnc_is_alive = True
        except:
            self.linuxcnc_is_alive = False

        self.linuxcnc_errors = linuxcnc.error_channel()
        lastLCNCerror = ""
        self.errorid = 0
        
        # begin the poll-update loop of the linuxcnc system
        self.scheduler = tornado.ioloop.PeriodicCallback( self.poll_update, UpdateStatusPollPeriodInMilliSeconds, io_loop=main_loop )
        self.scheduler.start()
        
        # begin the poll-update loop of the linuxcnc system
        self.scheduler_errors = tornado.ioloop.PeriodicCallback( self.poll_update_errors, UpdateErrorPollPeriodInMilliseconds, io_loop=main_loop )
        self.scheduler_errors.start()
        
        # register listeners
        self.observers = []
        self.hal_observers = []
        
        # HAL dictionaries of signals and pins
        self.pin_dict = {}
        self.sig_dict = {}
        
        self.counter = 0
        
        self.hal_poll_init()
        

    def add_observer(self, callback):
        self.observers.append(callback)

    def del_observer(self, callback):
        self.observers.remove(callback)

    def add_hal_observer(self, callback):
        self.hal_observers.append(callback)

    def del_hal_observer(self, callback):
        self.hal_observers.remove(callback)

    def clear_all(self, matching_connection):
        self.obervers = []

    def hal_poll_init(self):

        # halcmd can take 200ms or more to run, so run poll updates in a thread so as not to slow the server
        # requests for hal pins and sigs will read the results from the most recent update
        def hal_poll_thread(self):
            global instance_number
            myinstance = instance_number
            pollStartDelay = 0

            while (myinstance == instance_number):
                
                # first, check if linuxcnc is running at all
                if (not os.path.isfile( '/tmp/linuxcnc.lock' )):
                    pollStartDelay = 0
                    self.hal_mutex.acquire()
                    try:
                        self.linuxcnc_is_alive = False
                        self.pin_dict = {}
                        self.sig_dict = {}
                        self.linuxcnc_errors = None
                        self.linuxcnc_status = None
                        linuxcnc_command = None
                    finally:
                        self.hal_mutex.release()
                    time.sleep(UpdateHALPollPeriodInMilliSeconds/1000.0)
                    continue
                else:
                    if (pollStartDelay < 500):
                        # Delay on the first time linuxCNC lock file is present, so as not to call halcmd at the same time linuxcnc is starting
                        # But also keep checking if the lock file has disappeared, so we don't ever use a stale linuxcnc_status channel
                        time.sleep(0.01)
                        pollStartDelay = pollStartDelay + 1
                        continue
                    else:
                        self.linuxcnc_is_alive = True    

                self.p = subprocess.Popen( ['halcmd', '-s', 'show', 'pin'] , stderr=subprocess.PIPE, stdout=subprocess.PIPE )
                rawtuple = self.p.communicate()
                if ( len(rawtuple[0]) <= 0 ):
                    time.sleep(UpdateHALPollPeriodInMilliSeconds/1000.0)
                    continue
                raw = rawtuple[0].split('\n')

                pins = [ filter( lambda a: a != '', [x.strip() for x in line.split(' ')] ) for line in raw ]

                # UPDATE THE DICTIONARY OF PIN INFO
                # Acquire the mutex so we don't step on other threads
                self.hal_mutex.acquire()
                try:
                    self.pin_dict = {}
                    self.sig_dict = {}

                    for p in pins:
                        if len(p) > 5:
                            # if there is a signal listed on this pin, make sure
                            # that signal is in our signal dictionary
                            self.sig_dict[ p[6] ] = p[3]
                        if len(p) >= 5:
                            self.pin_dict[ p[4] ] = p[3]
                finally:
                    self.hal_mutex.release()

                # before starting the next check, sleep a little so we don't use all the CPU
                time.sleep(UpdateHALPollPeriodInMilliSeconds/1000.0)
            print "HAL Monitor exiting... ",myinstance, instance_number

        #Main part of hal_poll_init:
        # Create a thread for checking the HAL pins and sigs
        self.hal_mutex = threading.Lock()
        self.hal_thread = threading.Thread(target = hal_poll_thread, args=(self,))
        self.hal_thread.start()

    def poll_update_errors(self):
        global lastLCNCerror

        if (self.linuxcnc_is_alive is False):
            return
        
        if ( (self.linuxcnc_status is None) ):
            self.linuxcnc_errors = linuxcnc.error_channel()
        try:    
            error = self.linuxcnc_errors.poll()

            if error:
                kind, text = error
                if kind in (linuxcnc.NML_ERROR, linuxcnc.OPERATOR_ERROR):
                    typus = "error"
                else:
                    typus = "info"
                lastLCNCerror = { "kind":kind, "type":typus, "text":text, "time":strftime("%Y-%m-%d %H:%M:%S"), "id":self.errorid }

                self.errorid = self.errorid + 1 
        except:
            pass

    def poll_update(self):
        global linuxcnc_command

        # update linuxcnc status
        if (self.linuxcnc_is_alive):
            try:
                if ( self.linuxcnc_status is None ):
                    self.linuxcnc_status = linuxcnc.stat()
                    linuxcnc_command = linuxcnc.command()
                self.linuxcnc_status.poll()
            except:
                self.linuxcnc_status = None
                linuxcnc_command = None

        # notify all obervers of new status data poll
        for observer in self.observers:
            try:
                
                observer()
            except Exception as ex:
                self.del_observer(observer)


# *****************************************************
# Global LinuxCNCStatus Polling Object
# *****************************************************
LINUXCNCSTATUS = []

    

# *****************************************************
# Class to track an individual status item
# *****************************************************
class StatusItem( object ):

    def __init__( self, name=None, valtype='', help='', watchable=True, isarray=False, arraylen=0, requiresLinuxCNCUp=True, coreLinuxCNCVariable=True, isAsync=False ):
        self.name = name
        self.valtype = valtype
        self.help = help
        self.isarray = isarray
        self.arraylength = arraylen
        self.watchable = watchable
        self.requiresLinuxCNCUp = requiresLinuxCNCUp
        self.coreLinuxCNCVariable = coreLinuxCNCVariable
        self.isasync = isAsync

    @staticmethod
    def from_name( name ):
        global StatusItems
        val = StatusItems.get( name, None )
        if val is not None:
            return val
        if name.find('halpin_') is 0:
            return StatusItem( name=name, valtype='halpin', help='HAL pin.', isarray=False )
        elif name.find('halsig_') is 0:
            return StatusItem( name=name, valtype='halsig', help='HAL signal.', isarray=False )
        return None

    # puts this object into the dictionary, with the key == self.name
    def register_in_dict( self, dictionary ):
        dictionary[ self.name ] = self

    def to_json_compatible_form( self ):
        return self.__dict__

    def backplot_async( self, async_buffer, async_lock, linuxcnc_status_poller ):

        global lastBackplotFilename
        global lastBackplotData
        
        def do_backplot( self, async_buffer, async_lock, filename ):
            global MAX_BACKPLOT_LINES
            global lastBackplotFilename
            global lastBackplotData
            global BackplotLock

            BackplotLock.acquire()
            try:
                if (lastBackplotFilename != filename):
                    gr = GCodeReader.GCodeRender( INI_FILENAME )
                    gr.load()
                    lastBackplotData = gr.to_json(maxlines=MAX_BACKPLOT_LINES)
                    lastBackplotFilename = filename
                reply = {'data':lastBackplotData, 'code':LinuxCNCServerCommand.REPLY_COMMAND_OK }
            except ex:
                reply = {'data':'','code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND }
                print ex
            BackplotLock.release()

            async_lock.acquire()
            async_buffer.append(reply)
            async_lock.release()
            return

        if (( async_buffer is None ) or ( async_lock is None)):
            return { 'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND,'data':'' }

        if (lastBackplotFilename == linuxcnc_status_poller.linuxcnc_status.file):
            return {'data':lastBackplotData, 'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        
        thread = threading.Thread(target=do_backplot, args=(self, async_buffer, async_lock, linuxcnc_status_poller.linuxcnc_status.file))
        thread.start()
        return { 'code':LinuxCNCServerCommand.REPLY_COMMAND_OK, 'data':'' } 

    def backplot( self ):
        global MAX_BACKPLOT_LINES
        global BackplotLock
        reply = ""
        BackplotLock.acquire()
        try:
            gr = GCodeReader.GCodeRender( INI_FILENAME )
            gr.load()
            reply = gr.to_json(maxlines=MAX_BACKPLOT_LINES);
        except ex:
            print ex
        BackplotLock.release()
        return reply

    def read_gcode_file( self, filename ):
        try:
            f = open(filename, 'r')
            ret = f.read()
        except:
            ret = ""
        finally:
            f.close()
        return ret

    @staticmethod
    def get_ini_data_item(section, item_name):
        try:
            reply = StatusItem.get_ini_data( only_section=section.strip(), only_name=item_name.strip() )
        except Exception as ex:
            reply = {'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND,'data':''}
        return reply        

    # called in a "get_config" command to read the config file and output it's values
    @staticmethod
    def get_ini_data( only_section=None, only_name=None ):
        global INIFileDataTemplate
        global INI_FILENAME
        global INI_FILE_PATH         
        INIFileData = deepcopy(INIFileDataTemplate)
       
        sectionRegEx = re.compile( r"^\s*\[\s*(.+?)\s*\]" )
        keyValRegEx = re.compile( r"^\s*(.+?)\s*=\s*(.+?)\s*$" )
        try:
            section = 'NONE'
            comments = ''
            idv = 1
            with open( INI_FILENAME ) as file_:
                for line in file_:
                    if  line.lstrip().find('#') == 0 or line.lstrip().find(';') == 0:
                        comments = comments + line[1:]
                    else:
                        mo = sectionRegEx.search( line )
                        if mo:
                            section = mo.group(1)
                            hlp = ''
                            try:
                                if (section in ConfigHelp):
                                    hlp = ConfigHelp[section]['']['help'].encode('ascii','replace')
                            except:
                                pass
                            if (only_section is None or only_section == section):
                                INIFileData['sections'][section] = { 'comment':comments, 'help':hlp }
                            comments = '' 
                        else:
                            mo = keyValRegEx.search( line )
                            if mo:
                                hlp = ''
                                default = ''
                                try:
                                    if (section in ConfigHelp):
                                        if (mo.group(1) in ConfigHelp[section]):
                                            hlp = ConfigHelp[section][mo.group(1)]['help'].encode('ascii','replace')
                                            default = ConfigHelp[section][mo.group(1)]['default'].encode('ascii','replace')
                                except:
                                    pass

                                if (only_section is None or (only_section == section and only_name == mo.group(1) )):
                                    INIFileData['parameters'].append( { 'id':idv, 'values':{ 'section':section, 'name':mo.group(1), 'value':mo.group(2), 'comment':comments, 'help':hlp, 'default':default } } )
                                comments = ''
                                idv = idv + 1
            reply = {'data':INIFileData,'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        except Exception as ex:
            reply = {'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND,'data':''}

        return reply

    @staticmethod
    def check_hal_file_listed_in_ini( filename ):
        # check this is a valid hal file name
        f = filename
        found = False
        halfiles = StatusItem.get_ini_data( only_section='HAL', only_name='HALFILE' )
        halfiles = halfiles['data']['parameters']
        for v in halfiles:
            if (v['values']['value'] == f):
                found = True
                break
        if not found:
            halfiles = StatusItem.get_ini_data( only_section='HAL', only_name='POSTGUI_HALFILE' )
            halfiles = halfiles['data']['parameters']
            for v in halfiles:
                if (v['values']['value'] == f):
                    found = True
                    break
        return found

    def get_client_config( self ):
        global CONFIG_FILENAME
        reply = { 'code': LinuxCNCServerCommand.REPLY_COMMAND_OK }
        reply['data'] = '{}'

        try:
            fo = open( CONFIG_FILENAME, 'r' )
            reply['data'] = fo.read()
        except:
            reply['data'] = '{}'
        finally:
            try:
                fo.close()
            except:
                pass
        return reply


    def get_hal_file( self, filename ): 
        global INI_FILENAME
        global INI_FILE_PATH        
        try:

            reply = { 'code': LinuxCNCServerCommand.REPLY_COMMAND_OK }
            # strip off just the filename, if a path was given
            # we will only look in the config directory, so we ignore path
            [h,f] = os.path.split( filename )
            if not StatusItem.check_hal_file_listed_in_ini( f ):
                reply['code']= LinuxCNCServerCommand.REPLY_ERROR_INVALID_PARAMETER
                return reply

            reply['data'] = ''

            try:
                fo = open( os.path.join( INI_FILE_PATH, f ), 'r' )
                reply['data'] = fo.read()
            except:
                reply['data'] = ''
            finally:
                try:
                    fo.close()
                except:
                    pass
            
        except Exception as ex:
            reply['data'] = ''
            reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND

        return reply

    def list_gcode_files( self, directory ):
        file_list = []
        code = LinuxCNCServerCommand.REPLY_COMMAND_OK
        try:
            if directory is None:
                directory = "."
                directory = StatusItem.get_ini_data( only_section='DISPLAY', only_name='PROGRAM_PREFIX' )['data']['parameters'][0]['values']['value']
        except:
            pass
        try:
            file_list = glob.glob(  os.path.join(directory,'*.ngc') )
        except:
            code = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
        return { "code":code, "data":file_list, "directory":directory }

    def get_users( self ):
        global userdict
        return  { "code":LinuxCNCServerCommand.REPLY_COMMAND_OK, "data":userdict.keys() }

    def get_halgraph( self ):
        ret = { "code":LinuxCNCServerCommand.REPLY_COMMAND_OK, "data":"" }
        try:
            analyzer = MakeHALGraph.HALAnalyzer()
            analyzer.parse_pins()
            analyzer.write_svg( os.path.join(application_path,"static/halgraph.svg") )
            ret['data'] = 'static/halgraph.svg'
        except:        
            ret['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            ret['data'] = ''
        return ret


    # called in on_new_poll to update the current value of a status item
    def get_cur_status_value( self, linuxcnc_status_poller, item_index, command_dict, async_buffer=None, async_lock=None ):
        global lastLCNCerror
        ret = { "code":LinuxCNCServerCommand.REPLY_COMMAND_OK, "data":"" } 
        try:
            if (self.name == 'running'):
                if linuxcnc_status_poller.linuxcnc_is_alive:
                    ret['data'] = 1
                else:
                    ret['data'] = 0
                return ret
                
            if (not linuxcnc_status_poller.linuxcnc_is_alive and self.requiresLinuxCNCUp ):
                ret = { "code":LinuxCNCServerCommand.REPLY_LINUXCNC_NOT_RUNNING, "data":"Server is not running." }
                return ret

            if (not self.coreLinuxCNCVariable):

                # these are the "special" variables, not using the LinuxCNC status object
                if (self.name.find('halpin_') is 0):
                    linuxcnc_status_poller.hal_mutex.acquire()
                    try:
                        ret['data'] = linuxcnc_status_poller.pin_dict.get( self.name[7:], LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER )
                        if ( ret['data'] == LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER ):
                            ret['code'] = ret['data']
                    finally:
                        linuxcnc_status_poller.hal_mutex.release()
                elif (self.name.find('halsig_') is 0):
                    linuxcnc_status_poller.hal_mutex.acquire()
                    try:
                        ret['data'] = linuxcnc_status_poller.sig_dict.get( self.name[7:], LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER )
                        if ( ret['data'] == LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER ):
                            ret['code'] = ret['data']
                    finally:
                        linuxcnc_status_poller.hal_mutex.release()
                elif (self.name.find('backplot_async') is 0):
                     ret = self.backplot_async(async_buffer, async_lock,linuxcnc_status_poller)
                elif (self.name.find('backplot') is 0):
                    ret['data'] = self.backplot()
                elif (self.name == 'ini_file_name'):
                    ret['data'] = INI_FILENAME
                elif (self.name == 'file_content'):
                    ret['data'] = self.read_gcode_file(linuxcnc_status_poller.linuxcnc_status.file)
                elif (self.name == 'ls'):
                    ret = self.list_gcode_files( command_dict.get("directory", None) )
                elif (self.name == 'halgraph'):
                    ret = self.get_halgraph()
                elif (self.name == 'config'):
                    ret = StatusItem.get_ini_data()
                elif (self.name == 'config_item'):
                    ret = StatusItem.get_ini_data_item(command_dict.get("section", ''),command_dict.get("parameter", ''))
                elif (self.name == 'halfile'):
                    ret = self.get_hal_file( command_dict.get("filename", '') )
                elif (self.name == 'client_config'):
                    ret = self.get_client_config()
                elif (self.name == 'users'):
                    ret = self.get_users()
                elif (self.name == 'error'):
                    ret['data'] = lastLCNCerror
            else:
                # Variables that use the LinuxCNC status poller
                if (self.isarray):
                    ret['data'] = (linuxcnc_status_poller.linuxcnc_status.__getattribute__( self.name ))[item_index]
                else:
                    ret['data'] = linuxcnc_status_poller.linuxcnc_status.__getattribute__( self.name )
        except Exception as ex :
            ret['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            ret['data'] = ''
        return ret

tool_table_entry_type = type( linuxcnc.stat().tool_table[0] )
tool_table_length = len(linuxcnc.stat().tool_table)
axis_length = len(linuxcnc.stat().axis)
class StatusItemEncoder(json.JSONEncoder):
    def default(self, obj):
        global tool_table_entry_type
        if isinstance(obj, tool_table_entry_type):
            return list(obj)
        if isinstance(obj, StatusItem):
            return obj.to_json_compatible_form()
        if isinstance(obj, CommandItem):
            return { "name":obj.name, "paramTypes":obj.paramTypes, "help":obj.help }
        return json.JSONEncoder.default(self, obj)



# *****************************************************
# Global list of possible status items from linuxcnc
# *****************************************************
StatusItems = {}
StatusItem( name='acceleration',             watchable=True, valtype='float',   help='Default acceleration.  Reflects INI file value [TRAJ]DEFAULT_ACCELERATION' ).register_in_dict( StatusItems )
StatusItem( name='active_queue',             watchable=True, valtype='int'  ,   help='Number of motions blending' ).register_in_dict( StatusItems )
StatusItem( name='actual_position',          watchable=True, valtype='float[]', help='Current trajectory position. Array of floats: (x y z a b c u v w). In machine units.' ).register_in_dict( StatusItems )
StatusItem( name='adaptive_feed_enabled',    watchable=True, valtype='int',     help='status of adaptive feedrate override' ).register_in_dict( StatusItems )
StatusItem( name='ain',                      watchable=True, valtype='float[]', help='current value of the analog input pins' ).register_in_dict( StatusItems )
StatusItem( name='angular_units',            watchable=True, valtype='string' , help='From [TRAJ]ANGULAR_UNITS ini value' ).register_in_dict( StatusItems )
StatusItem( name='aout',                     watchable=True, valtype='float[]', help='Current value of the analog output pins' ).register_in_dict( StatusItems )
StatusItem( name='axes',                     watchable=True, valtype='int' ,    help='From [TRAJ]AXES ini value' ).register_in_dict( StatusItems )
StatusItem( name='axis_mask',                watchable=True, valtype='int' ,    help='Mask of axis available. X=1, Y=2, Z=4 etc.' ).register_in_dict( StatusItems )
StatusItem( name='block_delete',             watchable=True, valtype='bool' ,   help='Block delete currently on/off' ).register_in_dict( StatusItems )
StatusItem( name='command',                  watchable=True, valtype='string' , help='Currently executing command' ).register_in_dict( StatusItems )
StatusItem( name='current_line',             watchable=True, valtype='int' ,    help='Currently executing line' ).register_in_dict( StatusItems )
StatusItem( name='current_vel',              watchable=True, valtype='float' ,  help='Current velocity in cartesian space' ).register_in_dict( StatusItems )
StatusItem( name='cycle_time',               watchable=True, valtype='float' ,  help='From [TRAJ]CYCLE_TIME ini value' ).register_in_dict( StatusItems )
StatusItem( name='debug',                    watchable=True, valtype='int' ,    help='Debug flag' ).register_in_dict( StatusItems )
StatusItem( name='delay_left',               watchable=True, valtype='float' ,  help='remaining time on dwell (G4) command, seconds' ).register_in_dict( StatusItems )
StatusItem( name='din',                      watchable=True, valtype='int[]' ,  help='current value of the digital input pins' ).register_in_dict( StatusItems )
StatusItem( name='distance_to_go',           watchable=True, valtype='float' ,  help='remaining distance of current move, as reported by trajectory planner, in cartesian space' ).register_in_dict( StatusItems )
StatusItem( name='dout',                     watchable=True, valtype='int[]' ,  help='current value of the digital output pins' ).register_in_dict( StatusItems )
StatusItem( name='dtg',                      watchable=True, valtype='float[]', help='remaining distance of current move, as reported by trajectory planner, as a pose (tuple of 9 floats). ' ).register_in_dict( StatusItems )
StatusItem( name='echo_serial_number',       watchable=True, valtype='int' ,    help='The serial number of the last completed command sent by a UI to task. All commands carry a serial number. Once the command has been executed, its serial number is reflected in echo_serial_number' ).register_in_dict( StatusItems )
StatusItem( name='enabled',                  watchable=True, valtype='int' ,    help='trajectory planner enabled flag' ).register_in_dict( StatusItems )
StatusItem( name='estop',                    watchable=True, valtype='int' ,    help='estop flag' ).register_in_dict( StatusItems )
StatusItem( name='exec_state',               watchable=True, valtype='int' ,    help='Task execution state.  EMC_TASK_EXEC_ERROR = 1, EMC_TASK_EXEC_DONE = 2, EMC_TASK_EXEC_WAITING_FOR_MOTION = 3, EMC_TASK_EXEC_WAITING_FOR_MOTION_QUEUE = 4,EMC_TASK_EXEC_WAITING_FOR_IO = 5, EMC_TASK_EXEC_WAITING_FOR_MOTION_AND_IO = 7,EMC_TASK_EXEC_WAITING_FOR_DELAY = 8, EMC_TASK_EXEC_WAITING_FOR_SYSTEM_CMD = 9, EMC_TASK_EXEC_WAITING_FOR_SPINDLE_ORIENTED = 10' ).register_in_dict( StatusItems )
StatusItem( name='feed_hold_enabled',        watchable=True, valtype='int' ,    help='enable flag for feed hold' ).register_in_dict( StatusItems )
StatusItem( name='feed_override_enabled',    watchable=True, valtype='int' ,    help='enable flag for feed override' ).register_in_dict( StatusItems )
StatusItem( name='feedrate',                 watchable=True, valtype='float' ,  help='current feedrate' ).register_in_dict( StatusItems )
StatusItem( name='file',                     watchable=True, valtype='string' , help='currently executing gcode file' ).register_in_dict( StatusItems )
StatusItem( name='file_content',             coreLinuxCNCVariable=False, watchable=False,valtype='string' , help='currently executing gcode file contents' ).register_in_dict( StatusItems )
StatusItem( name='flood',                    watchable=True, valtype='int' ,    help='flood enabled' ).register_in_dict( StatusItems )
StatusItem( name='g5x_index',                watchable=True, valtype='int' ,    help='currently active coordinate system, G54=0, G55=1 etc.' ).register_in_dict( StatusItems )
StatusItem( name='g5x_offset',               watchable=True, valtype='float[]', help='offset of the currently active coordinate system, a pose' ).register_in_dict( StatusItems )
StatusItem( name='g92_offset',               watchable=True, valtype='float[]', help='pose of the current g92 offset' ).register_in_dict( StatusItems )
StatusItem( name='gcodes',                   watchable=True, valtype='int[]' ,  help='currently active G-codes. Tuple of 16 ints.' ).register_in_dict( StatusItems )
StatusItem( name='homed',                    watchable=True, valtype='int' ,    help='flag for homed state' ).register_in_dict( StatusItems )
StatusItem( name='id',                       watchable=True, valtype='int' ,    help='currently executing motion id' ).register_in_dict( StatusItems )
StatusItem( name='inpos',                    watchable=True, valtype='int' ,    help='machine-in-position flag' ).register_in_dict( StatusItems )
StatusItem( name='input_timeout',            watchable=True, valtype='int' ,    help='flag for M66 timer in progress' ).register_in_dict( StatusItems )
StatusItem( name='interp_state',             watchable=True, valtype='int' ,    help='Current state of RS274NGC interpreter.  EMC_TASK_INTERP_IDLE = 1,EMC_TASK_INTERP_READING = 2,EMC_TASK_INTERP_PAUSED = 3,EMC_TASK_INTERP_WAITING = 4' ).register_in_dict( StatusItems )
StatusItem( name='interpreter_errcode',      watchable=True, valtype='int' ,    help='Current RS274NGC interpreter return code. INTERP_OK=0, INTERP_EXIT=1, INTERP_EXECUTE_FINISH=2, INTERP_ENDFILE=3, INTERP_FILE_NOT_OPEN=4, INTERP_ERROR=5' ).register_in_dict( StatusItems )
StatusItem( name='joint_actual_position',    watchable=True, valtype='float[]' ,help='Actual joint positions' ).register_in_dict( StatusItems )
StatusItem( name='joint_position',           watchable=True, valtype='float[]', help='Desired joint positions' ).register_in_dict( StatusItems )
StatusItem( name='kinematics_type',          watchable=True, valtype='int' ,    help='identity=1, serial=2, parallel=3, custom=4 ' ).register_in_dict( StatusItems )
StatusItem( name='limit',                    watchable=True, valtype='int[]' ,  help='Tuple of axis limit masks. minHardLimit=1, maxHardLimit=2, minSoftLimit=4, maxSoftLimit=8' ).register_in_dict( StatusItems )
StatusItem( name='linear_units',             watchable=True, valtype='int' ,    help='reflects [TRAJ]LINEAR_UNITS ini value' ).register_in_dict( StatusItems )
StatusItem( name='lube',                     watchable=True, valtype='int' ,    help='lube on flag' ).register_in_dict( StatusItems )
StatusItem( name='lube_level',               watchable=True, valtype='int' ,    help='reflects iocontrol.0.lube_level' ).register_in_dict( StatusItems )
StatusItem( name='max_acceleration',         watchable=True, valtype='float' ,  help='Maximum acceleration. reflects [TRAJ]MAX_ACCELERATION ' ).register_in_dict( StatusItems )
StatusItem( name='max_velocity',             watchable=True, valtype='float' ,  help='Maximum velocity, float. reflects [TRAJ]MAX_VELOCITY.' ).register_in_dict( StatusItems )
StatusItem( name='mcodes',                   watchable=True, valtype='int[]' ,  help='currently active M-codes. tuple of 10 ints.' ).register_in_dict( StatusItems )
StatusItem( name='mist',                     watchable=True, valtype='int' ,    help='mist on flag' ).register_in_dict( StatusItems )
StatusItem( name='motion_line',              watchable=True, valtype='int' ,    help='source line number motion is currently executing' ).register_in_dict( StatusItems )
StatusItem( name='motion_mode',              watchable=True, valtype='int' ,    help='motion mode' ).register_in_dict( StatusItems )
StatusItem( name='motion_type',              watchable=True, valtype='int' ,    help='Trajectory planner mode. EMC_TRAJ_MODE_FREE = 1 = independent-axis motion, EMC_TRAJ_MODE_COORD = 2 coordinated-axis motion, EMC_TRAJ_MODE_TELEOP = 3 = velocity based world coordinates motion' ).register_in_dict( StatusItems )
StatusItem( name='optional_stop',            watchable=True, valtype='int' ,    help='option stop flag' ).register_in_dict( StatusItems )
StatusItem( name='paused',                   watchable=True, valtype='int' ,    help='motion paused flag' ).register_in_dict( StatusItems )
StatusItem( name='pocket_prepped',           watchable=True, valtype='int' ,    help='A Tx command completed, and this pocket is prepared. -1 if no prepared pocket' ).register_in_dict( StatusItems )
StatusItem( name='position',                 watchable=True, valtype='float[]', help='Trajectory position, a pose.' ).register_in_dict( StatusItems )
StatusItem( name='probe_tripped',            watchable=True, valtype='int' ,    help='Flag, true if probe has tripped (latch)' ).register_in_dict( StatusItems )
StatusItem( name='probe_val',                watchable=True, valtype='int' ,    help='reflects value of the motion.probe-input pin' ).register_in_dict( StatusItems )
StatusItem( name='probed_position',          watchable=True, valtype='float[]', help='position where probe tripped' ).register_in_dict( StatusItems )
StatusItem( name='probing',                  watchable=True, valtype='int' ,    help='flag, true if a probe operation is in progress' ).register_in_dict( StatusItems )
StatusItem( name='program_units',            watchable=True, valtype='int' ,    help='one of CANON_UNITS_INCHES=1, CANON_UNITS_MM=2, CANON_UNITS_CM=3' ).register_in_dict( StatusItems )
StatusItem( name='queue',                    watchable=True, valtype='int' ,    help='current size of the trajectory planner queue' ).register_in_dict( StatusItems )
StatusItem( name='queue_full',               watchable=True, valtype='int' ,    help='the trajectory planner queue is full' ).register_in_dict( StatusItems )
StatusItem( name='read_line',                watchable=True, valtype='int' ,    help='line the RS274NGC interpreter is currently reading' ).register_in_dict( StatusItems )
StatusItem( name='rotation_xy',              watchable=True, valtype='float' ,  help='current XY rotation angle around Z axis' ).register_in_dict( StatusItems )
StatusItem( name='settings',                 watchable=True, valtype='float[]', help='Current interpreter settings.  settings[0] = sequence number, settings[1] = feed rate, settings[2] = speed' ).register_in_dict( StatusItems )
StatusItem( name='spindle_brake',            watchable=True, valtype='int' ,    help='spindle brake flag' ).register_in_dict( StatusItems )
StatusItem( name='spindle_direction',        watchable=True, valtype='int' ,    help='rotational direction of the spindle. forward=1, reverse=-1' ).register_in_dict( StatusItems )
StatusItem( name='spindle_enabled',          watchable=True, valtype='int' ,    help='spindle enabled flag' ).register_in_dict( StatusItems )
StatusItem( name='spindle_increasing',       watchable=True, valtype='int' ,    help='' ).register_in_dict( StatusItems )
StatusItem( name='spindle_override_enabled', watchable=True, valtype='int' ,    help='spindle override enabled flag' ).register_in_dict( StatusItems )
StatusItem( name='spindle_speed',            watchable=True, valtype='float' ,  help='spindle speed value, rpm, > 0: clockwise, < 0: counterclockwise' ).register_in_dict( StatusItems )
StatusItem( name='spindlerate',              watchable=True, valtype='float' ,  help='spindle speed override scale' ).register_in_dict( StatusItems )
StatusItem( name='state',                    watchable=True, valtype='int' ,    help='current command execution status, int. One of RCS_DONE=1, RCS_EXEC=2, RCS_ERROR=3' ).register_in_dict( StatusItems )
StatusItem( name='task_mode',                watchable=True, valtype='int' ,    help='current task mode, int. one of MODE_MDI=3, MODE_AUTO=2, MODE_MANUAL=1' ).register_in_dict( StatusItems )
StatusItem( name='task_paused',              watchable=True, valtype='int' ,    help='task paused flag' ).register_in_dict( StatusItems )
StatusItem( name='task_state',               watchable=True, valtype='int' ,    help='Current task state. one of STATE_ESTOP=1, STATE_ESTOP_RESET=2, STATE_ON=4, STATE_OFF=3' ).register_in_dict( StatusItems )
StatusItem( name='tool_in_spindle',          watchable=True, valtype='int' ,    help='current tool number' ).register_in_dict( StatusItems )
StatusItem( name='tool_offset',              watchable=True, valtype='float' ,  help='offset values of the current tool' ).register_in_dict( StatusItems )
StatusItem( name='velocity',                 watchable=True, valtype='float' ,  help='default velocity, float. reflects [TRAJ]DEFAULT_VELOCITY' ).register_in_dict( StatusItems )

StatusItem( name='ls',                       coreLinuxCNCVariable=False, watchable=True, valtype='string[]',help='Get a list of gcode files.  Optionally specify directory with "directory":"string", or default directory will be used.  Only *.ngc files will be listed.' ).register_in_dict( StatusItems )
StatusItem( name='backplot',                 coreLinuxCNCVariable=False, watchable=False, valtype='string[]',help='Backplot information.  Potentially very large list of lines.' ).register_in_dict( StatusItems )
StatusItem( name='backplot_async',           coreLinuxCNCVariable=False, watchable=False, valtype='string[]', isAsync=True, help='Backplot information.  Potentially very large list of lines.' ).register_in_dict( StatusItems )
StatusItem( name='config',                   coreLinuxCNCVariable=False, watchable=False, valtype='dict',    help='Config (ini) file contents.', requiresLinuxCNCUp=False  ).register_in_dict( StatusItems )
StatusItem( name='config_item',              coreLinuxCNCVariable=False, watchable=False, valtype='dict',    help='Specific section/name from the config file.  Pass in section=??? and name=???.', requiresLinuxCNCUp=False  ).register_in_dict( StatusItems )
StatusItem( name='halfile',                  coreLinuxCNCVariable=False, watchable=False, valtype='string',  help='Contents of a hal file.  Pass in filename=??? to specify the hal file name', requiresLinuxCNCUp=False ).register_in_dict( StatusItems )
StatusItem( name='halgraph',                 coreLinuxCNCVariable=False, watchable=False, valtype='string',  help='Filename of the halgraph generated from the currently running instance of LinuxCNC.  Filename will be "halgraph.svg"' ).register_in_dict( StatusItems )
StatusItem( name='ini_file_name',            coreLinuxCNCVariable=False, watchable=True,  valtype='string',  help='INI file to use for next LinuxCNC start.', requiresLinuxCNCUp=False ).register_in_dict( StatusItems )
StatusItem( name='client_config',            coreLinuxCNCVariable=False, watchable=True,  valtype='string',  help='Client Configuration.', requiresLinuxCNCUp=False ).register_in_dict( StatusItems )
StatusItem( name='users',                    coreLinuxCNCVariable=False, watchable=True,  valtype='string',  help='Web server user list.', requiresLinuxCNCUp=False ).register_in_dict( StatusItems )

StatusItem( name='error',                    coreLinuxCNCVariable=False, watchable=True,  valtype='dict',    help='Error queue.' ).register_in_dict( StatusItems )
StatusItem( name='running',                  coreLinuxCNCVariable=False, watchable=True,  valtype='int',     help='True if linuxcnc is up and running.', requiresLinuxCNCUp=False ).register_in_dict( StatusItems )

# Array Status items
StatusItem( name='tool_table',               watchable=True, valtype='float[]', help='list of tool entries. Each entry is a sequence of the following fields: id, xoffset, yoffset, zoffset, aoffset, boffset, coffset, uoffset, voffset, woffset, diameter, frontangle, backangle, orientation', isarray=True, arraylen=tool_table_length ).register_in_dict( StatusItems )
StatusItem( name='axis',                     watchable=True, valtype='dict' ,   help='Axis Dictionary', isarray=True, arraylen=axis_length ).register_in_dict( StatusItems )


# *****************************************************
# Class to issue cnc commands
# *****************************************************
class CommandItem( object ):
    
    # Command types
    MOTION=0
    HAL=1
    SYSTEM=2
    
    def __init__( self, name=None, paramTypes=[], help='', command_type=MOTION ):
        self.name = name
        self.paramTypes = paramTypes
        self.help = help
        for idx in xrange(0, len(paramTypes)):
            paramTypes[idx]['ordinal'] = str(idx)
        self.type = command_type

    # puts this object into the dictionary, with the key == self.name
    def register_in_dict( self, dictionary ):
        dictionary[ self.name ] = self

    # called in a "put_config" command to write INI data to INI file, completely re-writing the file
    def put_ini_data( self, commandDict ):
        global INI_FILENAME
        global INI_FILE_PATH         
        reply = { 'code': LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND }
        try:
            # construct the section list
            sections = {}
            sections_sorted = []
            for line in commandDict['data']['parameters']:
                sections[line['values']['section']] = line['values']['section']
            for section in sections:
                sections_sorted.append( section )
            sections_sorted = sorted(sections_sorted)

            inifile = open(INI_FILENAME, 'w', 1)

            for section in sections_sorted:
                # write out the comments before the section header
                if (section in commandDict['data']['sections']):
                    commentlines = commandDict['data']['sections'][section]['comment'].split('\n')
                    for c_line in commentlines:
                        if len(c_line) > 0:
                            inifile.write( '#' + c_line + '\n' )

                #write the section header
                inifile.write( '[' + section + ']\n' )

                #write the key/value pairs
                for line in commandDict['data']['parameters']:
                    if line['values']['section'] == section :
                        if (len(line['values']['comment']) > 0):
                            commentlines = line['values']['comment'].split('\n')
                            for c_line in commentlines:
                                if len(c_line) > 0:
                                    inifile.write( '#' + c_line +'\n' )
                        if (len(line['values']['name']) > 0):
                            inifile.write( line['values']['name'] + '=' + line['values']['value'] + '\n' )
                inifile.write('\n\n')
            inifile.close()    
            reply['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
        except:
            reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
        finally:
            try:
                inifile.close()
            except:
                pass

        return reply

    def put_client_config( self, key, value ):
        global CONFIG_FILENAME
        reply = {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        
        try:
            fo = open( CONFIG_FILENAME, 'r' )
            jsonobj = json.loads( fo.read() );
            jsonobj[key] = value;
        except:
            jsonobj = {}
        finally:
            fo.close()
        
        try:    
            fo = open( CONFIG_FILENAME, 'w' )
            fo.write( json.dumps(jsonobj) )
        except:
            reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
        finally:
            try:
                fo.close()
            except:
                reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
                
        return reply
           

    def put_gcode_file( self, filename, data ):
        global linuxcnc_command

        reply = {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        try:
            
            # strip off just the filename, if a path was given
            # we will only look in the config directory, so we ignore path
            [h,f] = os.path.split( filename )

            path = StatusItem.get_ini_data( only_section='DISPLAY', only_name='PROGRAM_PREFIX' )['data']['parameters'][0]['values']['value']
            
            try:
                fo = open( os.path.join( path, f ), 'w' )
                fo.write(data)
            except:
                reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            finally:
                try:
                    fo.close()
                except:
                    reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND

            if (reply['code'] == LinuxCNCServerCommand.REPLY_COMMAND_OK):
                (linuxcnc_command.program_open( os.path.join( path, f ) ) )
            
        except Exception as ex:
            print ex
            reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
        return reply         

    # writes the specified HAL file to disk
    def put_hal_file( self, filename, data ):
        global INI_FILENAME
        global INI_FILE_PATH
        reply = {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        try:
            # strip off just the filename, if a path was given
            # we will only look in the config directory, so we ignore path
            [h,f] = os.path.split( filename )
            if not StatusItem.check_hal_file_listed_in_ini( f ):
                reply['code']=LinuxCNCServerCommand.REPLY_ERROR_INVALID_PARAMETER
                return reply

            try:
                fo = open( os.path.join( INI_FILE_PATH, f ), 'w' )
                fo.write(data)
            except:
                reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            finally:
                try:
                    fo.close()
                except:
                    reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            
        except Exception as ex: 
            reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
        
        return reply 
    

    def shutdown_linuxcnc( self ):
        try:
            displayname = StatusItem.get_ini_data( only_section='DISPLAY', only_name='DISPLAY' )['data']['parameters'][0]['values']['value']
            p = subprocess.Popen( ['pkill', displayname] , stderr=subprocess.STDOUT )
            return {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK }
        except:
            return {'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND }
        
    def start_linuxcnc( self ):
        global INI_FILENAME
        global INI_FILE_PATH
        p = subprocess.Popen(['pidof', '-x', 'linuxcnc'], stdout=subprocess.PIPE )
        result = p.communicate()[0]
        if len(result) > 0:
            return {'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND}
        subprocess.Popen(['linuxcnc', INI_FILENAME], stderr=subprocess.STDOUT )
        return {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}

    def add_user( self, username, password ):
        try:
            proc = subprocess.Popen(['python', 'AddUser.py', username, password], stderr=subprocess.STDOUT )
            proc.communicate()
            readUserList()
            return {'code':LinuxCNCServerCommand.REPLY_COMMAND_OK}
        except:
            pass

    def execute( self, passed_command_dict, linuxcnc_status_poller ):
        global INI_FILENAME
        global INI_FILE_PATH
        global lastLCNCerror
        global linuxcnc_command

        try:
            paramcnt = 0
            params = []

            if ((linuxcnc_command is None or (not linuxcnc_status_poller.linuxcnc_is_alive)) and not (self.type == CommandItem.SYSTEM)):
                return { 'code':LinuxCNCServerCommand.REPLY_LINUXCNC_NOT_RUNNING } 
            
            for paramDesc in self.paramTypes:
                paramval = passed_command_dict.get( paramDesc['pname'], None )
                if paramval is None:
                    paramval = passed_command_dict.get( paramDesc['ordinal'], None )
                paramtype = paramDesc['ptype']

                if (paramval is not None):
                    if (paramtype == 'lookup'):
                        params.append( linuxcnc.__getattribute__( paramval.strip() ) )
                    elif (paramtype == 'float'):
                        params.append( float( paramval ) )
                    elif (paramtype == 'int'):
                        params.append( int( paramval ) )
                    else:
                        params.append(paramval)
                else:
                    if not paramDesc['optional']:
                        return { 'code':LinuxCNCServerCommand.REPLY_MISSING_COMMAND_PARAMETER + ' ' + paramDesc['name'] }
                    else:
                        break

            if (self.type == CommandItem.MOTION):
                # execute command as a linuxcnc module call
                (linuxcnc_command.__getattribute__( self.name ))( *params )

            elif (self.type == CommandItem.HAL):
                # implement the command as a halcommand
                p = subprocess.Popen( ['halcmd'] + filter( lambda a: a != '', [x.strip() for x in params[0].split(' ')]), stderr=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=(1024*64) )
                stdouterr = p.communicate()
                reply = {}
                reply['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
                reply['data'] = {}
                reply['data']['out']=stdouterr[0]
                reply['data']['err']=stdouterr[1]
                return reply
            elif (self.type == CommandItem.SYSTEM):
                # command is a special system command
                reply = {}
                
                if (self.name == 'ini_file_name'):
                    INI_FILENAME = passed_command_dict.get( 'ini_file_name', INI_FILENAME )
                    [INI_FILE_PATH, x] = os.path.split( INI_FILENAME )
                    reply['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
                elif (self.name == 'config'): 
                    reply = self.put_ini_data(passed_command_dict)
                elif (self.name == 'clear_error'):
                    lastLCNCerror = ""
                elif (self.name == 'halfile'):
                    reply = self.put_hal_file( filename=passed_command_dict.get('filename',passed_command_dict['0']).strip(), data=passed_command_dict.get('data', passed_command_dict.get['1']) )
                elif (self.name == 'shutdown'):
                    reply = self.shutdown_linuxcnc()
                elif (self.name == 'startup'):
                    reply = self.start_linuxcnc()
                elif (self.name == 'program_upload'):
                    reply = self.put_gcode_file(filename=passed_command_dict.get('filename',passed_command_dict['0']).strip(), data=passed_command_dict.get('data', passed_command_dict['1']))
                elif (self.name == 'save_client_config'):
                    reply = self.put_client_config( (passed_command_dict.get('key', passed_command_dict.get('0'))), (passed_command_dict.get('value', passed_command_dict.get('1'))) );
                elif (self.name == 'add_user'):
                    reply = self.add_user( passed_command_dict.get('username',passed_command_dict['0']).strip(), passed_command_dict.get('password',passed_command_dict['1']).strip() )
                else:
                    reply['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
                return reply
            else:
                return { 'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND }

            return { 'code':LinuxCNCServerCommand.REPLY_COMMAND_OK }
        except:
            return { 'code':LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND }




# Custom Command Items
CommandItems = {}
CommandItem( name='halcmd',                  paramTypes=[ {'pname':'param_string', 'ptype':'string', 'optional':False} ],  help='Call halcmd. Results returned in a string.', command_type=CommandItem.HAL ).register_in_dict( CommandItems )
CommandItem( name='ini_file_name',           paramTypes=[ {'pname':'ini_file_name', 'ptype':'string', 'optional':False} ],  help='Set the INI file to use on next linuxCNC load.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )

# Pre-defined Command Items
CommandItem( name='abort',                   paramTypes=[],      help='send EMC_TASK_ABORT message' ).register_in_dict( CommandItems )
CommandItem( name='auto',                    paramTypes=[ {'pname':'auto', 'ptype':'lookup', 'lookup-vals':['AUTO_RUN','AUTO_STEP','AUTO_RESUME','AUTO_PAUSE'], 'optional':False }, {'pname':'run_from', 'ptype':'int', 'optional':True} ],      help='run, step, pause or resume a program.  auto legal values: AUTO_RUN, AUTO_STEP, AUTO_RESUME, AUTO_PAUSE' ).register_in_dict( CommandItems )
CommandItem( name='brake',                   paramTypes=[ {'pname':'onoff', 'ptype':'lookup', 'lookup-vals':['BRAKE_ENGAGE','BRAKE_RELEASE'], 'optional':False} ],      help='engage or release spindle brake.  Legal values: BRAKE_ENGAGE or BRAKE_RELEASE' ).register_in_dict( CommandItems )
CommandItem( name='debug',                   paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set debug level bit-mask via EMC_SET_DEBUG message' ).register_in_dict( CommandItems )
CommandItem( name='feedrate',                paramTypes=[ {'pname':'rate', 'ptype':'float', 'optional':False} ],      help='set the feedrate' ).register_in_dict( CommandItems )
CommandItem( name='flood',                   paramTypes=[ {'pname':'onoff', 'ptype':'lookup', 'lookup-vals':['FLOOD_ON','FLOOD_OFF'], 'optional':False} ],      help='turn on/off flood coolant.  Legal values: FLOOD_ON, FLOOD_OFF' ).register_in_dict( CommandItems )
CommandItem( name='home',                    paramTypes=[ {'pname':'axis', 'ptype':'int', 'optional':False} ],       help='home a given axis' ).register_in_dict( CommandItems )
CommandItem( name='jog',                     paramTypes=[ {'pname':'jog', 'ptype':'lookup', 'lookup-vals':['JOG_STOP','JOG_CONTINUOUS','JOG_INCREMENT'], 'optional':False}, { 'pname':'axis', 'ptype':'int', 'optional':False }, { 'pname':'velocity', 'ptype':'float', 'optional':True }, {'pname':'distance', 'ptype':'float', 'optional':True } ],      help='jog(command, axis[, velocity[, distance]]).  Legal values: JOG_STOP, JOG_CONTINUOUS, JOG_INCREMENT' ).register_in_dict( CommandItems )
CommandItem( name='load_tool_table',         paramTypes=[],      help='reload the tool table' ).register_in_dict( CommandItems )
CommandItem( name='maxvel',                  paramTypes=[ {'pname':'rate', 'ptype':'float', 'optional':False} ],      help='set maximum velocity' ).register_in_dict( CommandItems )
CommandItem( name='mdi',                     paramTypes=[ {'pname':'mdi', 'ptype':'string', 'optional':False} ],      help='send an MDI command. Maximum 255 chars' ).register_in_dict( CommandItems )
CommandItem( name='mist',                    paramTypes=[ {'pname':'onoff', 'ptype':'lookup', 'lookup-vals':['MIST_ON','MIST_OFF'], 'optional':False} ],       help='turn on/off mist.  Legal values: MIST_ON, MIST_OFF' ).register_in_dict( CommandItems )
CommandItem( name='mode',                    paramTypes=[ {'pname':'mode', 'ptype':'lookup', 'lookup-vals':['MODE_AUTO','MODE_MANUAL','MODE_MDI'], 'optional':False} ],      help='Set mode. Legal values: MODE_AUTO, MODE_MANUAL, MODE_MDI).' ).register_in_dict( CommandItems )
CommandItem( name='override_limits',         paramTypes=[],      help='set the override axis limits flag.' ).register_in_dict( CommandItems )
CommandItem( name='program_open',            paramTypes=[ {'pname':'filename', 'ptype':'string', 'optional':False}],      help='Open an NGC file.' ).register_in_dict( CommandItems )
CommandItem( name='program_upload',          paramTypes=[ {'pname':'filename', 'ptype':'string', 'optional':False}, {'pname':'data', 'ptype':'string', 'optional':False} ], command_type=CommandItem.SYSTEM, help='Create and open an NGC file.' ).register_in_dict( CommandItems )
CommandItem( name='reset_interpreter',       paramTypes=[],      help='reset the RS274NGC interpreter' ).register_in_dict( CommandItems )
CommandItem( name='set_adaptive_feed',       paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set adaptive feed flag ' ).register_in_dict( CommandItems )
CommandItem( name='set_analog_output',       paramTypes=[ {'pname':'index', 'ptype':'int', 'optional':False}, {'pname':'value', 'ptype':'float', 'optional':False} ],      help='set analog output pin to value' ).register_in_dict( CommandItems )
CommandItem( name='set_block_delete',        paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set block delete flag' ).register_in_dict( CommandItems )
CommandItem( name='set_digital_output',      paramTypes=[ {'pname':'index', 'ptype':'int', 'optional':False}, {'pname':'value', 'ptype':'int', 'optional':False} ],      help='set digital output pin to value' ).register_in_dict( CommandItems )
CommandItem( name='set_feed_hold',           paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set feed hold on/off' ).register_in_dict( CommandItems )
CommandItem( name='set_feed_override',       paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set feed override on/off' ).register_in_dict( CommandItems )
CommandItem( name='set_max_limit',           paramTypes=[ {'pname':'axis', 'ptype':'int', 'optional':False}, {'pname':'limit', 'ptype':'float', 'optional':False} ],      help='set max position limit for a given axis' ).register_in_dict( CommandItems )
CommandItem( name='set_max_limit',           paramTypes=[ {'pname':'axis', 'ptype':'int', 'optional':False}, {'pname':'limit', 'ptype':'float', 'optional':False} ],      help='set min position limit for a given axis' ).register_in_dict( CommandItems )
CommandItem( name='set_optional_stop',       paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set optional stop on/off ' ).register_in_dict( CommandItems )
CommandItem( name='set_spindle_override',    paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='set spindle override flag' ).register_in_dict( CommandItems )
CommandItem( name='spindle',                 paramTypes=[ {'pname':'spindle', 'ptype':'lookup', 'lookup-vals':['SPINDLE_FORWARD','SPINDLE_REVERSE','SPINDLE_OFF','SPINDLE_INCREASE','SPINDLE_DECREASE','SPINDLE_CONSTANT'], 'optional':False} ],      help='set spindle direction.  Legal values: SPINDLE_FORWARD, SPINDLE_REVERSE, SPINDLE_OFF, SPINDLE_INCREASE, SPINDLE_DECREASE, SPINDLE_CONSTANT' ).register_in_dict( CommandItems )
CommandItem( name='spindleoverride',         paramTypes=[ {'pname':'factor', 'ptype':'float', 'optional':False} ],      help='set spindle override factor' ).register_in_dict( CommandItems )
CommandItem( name='state',                   paramTypes=[ {'pname':'state', 'ptype':'lookup', 'lookup-vals':['STATE_ESTOP','STATE_ESTOP_RESET','STATE_ON','STATE_OFF'], 'optional':False} ],      help='set the machine state.  Legal values: STATE_ESTOP_RESET, STATE_ESTOP, STATE_ON, STATE_OFF' ).register_in_dict( CommandItems )
CommandItem( name='teleop_enable',           paramTypes=[ {'pname':'onoff', 'ptype':'int', 'optional':False} ],      help='enable/disable teleop mode' ).register_in_dict( CommandItems )
CommandItem( name='teleop_vector',           paramTypes=[ {'pname':'p1', 'ptype':'float', 'optional':False}, {'pname':'p2', 'ptype':'float', 'optional':False}, {'pname':'p3', 'ptype':'float', 'optional':False}, {'pname':'p4', 'ptype':'float', 'optional':True}, {'pname':'p5', 'ptype':'float', 'optional':True}, {'pname':'p6', 'ptype':'float', 'optional':True} ],      help='set teleop destination vector' ).register_in_dict( CommandItems )
CommandItem( name='tool_offset',             paramTypes=[ {'pname':'toolnumber', 'ptype':'int', 'optional':False}, {'pname':'z_offset', 'ptype':'float', 'optional':False}, {'pname':'x_offset', 'ptype':'float', 'optional':False}, {'pname':'diameter', 'ptype':'float', 'optional':False}, {'pname':'frontangle', 'ptype':'float', 'optional':False}, {'pname':'backangle', 'ptype':'float', 'optional':False}, {'pname':'orientation', 'ptype':'float', 'optional':False} ],      help='set the tool offset' ).register_in_dict( CommandItems )
CommandItem( name='traj_mode',               paramTypes=[ {'pname':'mode', 'ptype':'lookup', 'lookup-vals':['TRAJ_MODE_FREE','TRAJ_MODE_COORD','TRAJ_MODE_TELEOP'], 'optional':False} ],      help='set trajectory mode.  Legal values: TRAJ_MODE_FREE, TRAJ_MODE_COORD, TRAJ_MODE_TELEOP' ).register_in_dict( CommandItems )
CommandItem( name='unhome',                  paramTypes=[ {'pname':'axis', 'ptype':'int', 'optional':False} ],       help='unhome a given axis' ).register_in_dict( CommandItems )
CommandItem( name='wait_complete',           paramTypes=[ {'pname':'timeout', 'ptype':'float', 'optional':True} ],       help='wait for completion of the last command sent. If timeout in seconds not specified, default is 1 second' ).register_in_dict( CommandItems )

CommandItem( name='config',                  paramTypes=[ {'pname':'data', 'ptype':'dict', 'optional':False} ],       help='Overwrite the config file.  Parameter is a dictionary with the same format as returned from "get config"', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )
CommandItem( name='halfile',                 paramTypes=[ {'pname':'filename', 'ptype':'string', 'optional':False}, {'pname':'data', 'ptype':'string', 'optional':False} ],       help='Overwrite the specified file.  Parameter is a filename, then a string containing the new hal file contents.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )
CommandItem( name='clear_error',             paramTypes=[  ],       help='Clear the last error condition.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )
CommandItem( name='save_client_config',      paramTypes=[ {'pname':'key', 'ptype':'string', 'optional':False}, {'pname':'value', 'ptype':'string', 'optional':False} ],     help='Save a JSON object representing client configuration.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )

CommandItem( name='add_user',                paramTypes=[ {'pname':'username', 'ptype':'string', 'optional':False}, {'pname':'password', 'ptype':'string', 'optional':False} ], help='Add a user to the web server.  Set password to - to delete the user.  If all users are deleted, then a user named default, password=default will be created.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )

CommandItem( name='shutdown',                paramTypes=[ ],       help='Shutdown LinuxCNC system.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )
CommandItem( name='startup',                 paramTypes=[ ],       help='Start LinuxCNC system.', command_type=CommandItem.SYSTEM ).register_in_dict( CommandItems )

# *****************************************************
# Config file help
# *****************************************************
ConfigHelp = {
    'AXIS_0':{
        '':                 { 'default':'',          'help':'General parameters for the individual components in the axis control module. The axis section names begin numbering at 0, and run through the number of axes specified in the [TRAJ] AXES entry minus 1.' },
        'TYPE':             { 'default':'LINEAR',    'help':'The type of axes, either LINEAR or ANGULAR'},
        'WRAPPED_ROTARY':   { 'default':'1',         'help':'When this is set to 1 for an ANGULAR axis the axis will move 0-359.999 degrees. Positive Numbers will move the axis in a positive direction and negative numbers will move the axis in the negative direction.'},
        'LOCKING_INDEXER':  { 'default':'1',         'help':'When this is set to 1 a G0 move for this axis will initiate an unlock with axis.N.unlock pin then wait for the axis.N.is-unlocked pin then move the axis at the rapid rate for that axis. After the move the axis.N.unlock will be false and motion will wait for axis.N.is-unlocked to go false. Moving with other axes is not allowed when moving a locked rotary axis. '},
        'UNITS':            { 'default':'INCH',      'help':'If specified, this setting overrides the related [TRAJ] UNITS setting. (e.g., [TRAJ]LINEAR_UNITS if the TYPE of this axis is LINEAR, [TRAJ]ANGULAR_UNITS if the TYPE of this axis is ANGULAR) '},
        'MAX_VELOCITY':     { 'default':'1.2',       'help':'Maximum velocity for this axis in machine units per second.'},
        'MAX_ACCELERATION': { 'default':'20.0',      'help':'Maximum acceleration for this axis in machine units per second squared. '},
        'BACKLASH':         { 'default':'0.0000',    'help':'Backlash in machine units. Backlash compensation value can be used to make up for small deficiencies in the hardware used to drive an axis. If backlash is added to an axis and you are using steppers the STEPGEN_MAXACCEL must be increased to 1.5 to 2 times the MAX_ACCELERATION for the axis. '},
        'COMP_FILE':        { 'default':'file.extension',          'help':'A file holding compensation structure for the axis. The file could be named xscrew.comp, for example, for the X axis. File names are case sensitive and can contain letters and/or numbers. The values are triplets per line separated by a space. The first value is nominal (where it should be). The second and third values depend on the setting of COMP_FILE_TYPE. Currently the limit inside LinuxCNC is for 256 triplets per axis. If COMP_FILE is specified, BACKLASH is ignored. Compensation file values are in machine units. '},
        'COMP_FILE_TYPE':   { 'default':'0 or 1',    'help':'If 0: The second and third values specify the forward position (where the axis is while traveling forward) and the reverse position (where the axis is while traveling reverse), positions which correspond to the nominal position.          If 1: The second and third values specify the forward trim (how far from nominal while traveling forward) and the reverse trim (how far from nominal while traveling in reverse), positions which correspond to the nominal position.    Example triplet with COMP_FILE_TYPE = 0: 1.00 1.01 0.99 +    Example triplet with COMP_FILE_TYPE = 1: 1.00 0.01 -0.01'},
        'MIN_LIMIT':        { 'default':'-1000',     'help':'The minimum limit (soft limit) for axis motion, in machine units. When this limit is exceeded, the controller aborts axis motion. '},
        'MAX_LIMIT':        { 'default':'1000',      'help':'The maximum limit (soft limit) for axis motion, in machine units. When this limit is exceeded, the controller aborts axis motion. '},
        'MIN_FERROR':       { 'default':'0.010',     'help':'This is the value in machine units by which the axis is permitted to deviate from commanded position at very low speeds. If MIN_FERROR is smaller than FERROR, the two produce a ramp of error trip points. You could think of this as a graph where one dimension is speed and the other is permitted following error. As speed increases the amount of following error also increases toward the FERROR value. '},
        'FERROR':           { 'default':'1.0',       'help':'FERROR is the maximum allowable following error, in machine units. If the difference between commanded and sensed position exceeds this amount, the controller disables servo calculations, sets all the outputs to 0.0, and disables the amplifiers. If MIN_FERROR is present in the .ini file, velocity-proportional following errors are used. Here, the maximum allowable following error is proportional to the speed, with FERROR applying to the rapid rate set by [TRAJ]MAX_VELOCITY, and proportionally smaller following errors for slower speeds. The maximum allowable following error will always be greater than MIN_FERROR. This prevents small following errors for stationary axes from inadvertently aborting motion. Small following errors will always be present due to vibration, etc. The following polarity values determine how inputs are interpreted and how outputs are applied. They can usually be set via trial-and-error since there are only two possibilities. The LinuxCNC Servo Axis Calibration utility program (in the AXIS interface menu Machine/Calibration and in TkLinuxCNC it is under Setting/Calibration) can be used to set these and more interactively and verify their results so that the proper values can be put in the INI file with a minimum of trouble. '},
        'HOME':             { 'default':'0.0',       'help':'The position that the joint will go to upon completion of the homing sequence'},
        'HOME_OFFSET':      { 'default':'0.0',       'help':'The axis position of the home switch or index pulse, in machine units. When the home point is found during the homing process, this is the position that is assigned to that point. When sharing home and limit switches and using a home sequence that will leave the home/limit switch in the toggled state the home offset can be used define the home switch position to be other than 0 if your HOME position is desired to be 0. '},
        'HOME_SEARCH_VEL':  { 'default':'0.0',       'help':'Initial homing velocity in machine units per second. Sign denotes direction of travel. A value of zero means assume that the current location is the home position for the machine. If your machine has no home switches you will want to leave this value at zero. '},
        'HOME_LATCH_VEL':   { 'default':'0.0',       'help':'Homing velocity in machine units per second to the home switch latch position. Sign denotes direction of travel. '},
        'HOME_FINAL_VEL':   { 'default':'0.0',       'help':'Velocity in machine units per second from home latch position to home position. If left at 0 or not included in the axis rapid velocity is used. Must be a positive number. '},
        'HOME_USE_INDEX':   { 'default':'NO',        'help':'If the encoder used for this axis has an index pulse, and the motion card has provision for this signal you may set it to yes. When it is yes, it will affect the kind of home pattern used. Currently, you cant home to index with steppers unless youre using stepgen in velocity mode and PID.'},
        'HOME_IGNORE_LIMITS': { 'default':'NO',      'help':'When you use the limit switch as a home switch and the limit switch this should be set to YES. When set to YES the limit switch for this axis is ignored when homing. You must configure your homing so that at the end of your home move the home/limit switch is not in the toggled state you will get a limit switch error after the home move. '},
        'HOME_IS_SHARED':   { 'default':'<n>',       'help':'If the home input is shared by more than one axis set <n> to 1 to prevent homing from starting if the one of the shared switches is already closed. Set <n> to 0 to permit homing if a switch is closed. '},
        'HOME_SEQUENCE':    { 'default':'<n>',       'help':'Used to define the "Home All" sequence. <n> starts at 0 and no numbers may be skipped. If left out or set to -1 the joint will not be homed by the "Home All" function. More than one axis can be homed at the same time. '},
        'VOLATILE_HOME':    { 'default':'0',         'help':'When enabled (set to 1) this joint will be unhomed if the Machine Power is off or if E-Stop is on. This is useful if your machine has home switches and does not have position feedback such as a step and direction driven machine. '},
        'DEADBAND':         { 'default':'0.000015',  'help':'Might be used by a PID component and the assumption is that the output is volts.  How close is close enough to consider the motor in position, in machine units. '},
        'BIAS':             { 'default':'0.000',     'help':'Might be used by a PID component and the assumption is that the output is volts.  This is used by hm2-servo and some others. Bias is a constant amount that is added to the output. In most cases it should be left at zero. However, it can sometimes be useful to compensate for offsets in servo amplifiers, or to balance the weight of an object that moves vertically. bias is turned off when the PID loop is disabled, just like all other components of the output.'},
        'P':                { 'default':'50',        'help':'Might be used by a PID/servo component.  The proportional gain for the axis servo. This value multiplies the error between commanded and actual position in machine units, resulting in a contribution to the computed voltage for the motor amplifier. The units on the P gain are volts per machine unit, eg volts/unit'},
        'I':                { 'default':'0',         'help':'Might be used by a PID/servo component.  The integral gain for the axis servo. The value multiplies the cumulative error between commanded and actual position in machine units, resulting in a contribution to the computed voltage for the motor amplifier. The units on the I gain are volts per machine unit second, eg volts/(unit second)'},
        'D':                { 'default':'0',         'help':'Might be used by a PID/servo component.  The derivative gain for the axis servo. The value multiplies the difference between the current and previous errors, resulting in a contribution to the computed voltage for the motor amplifier. The units on the D gain are volts per machine unit per second, e.g. volts/(unit second)'},
        'FF0':              { 'default':'0',         'help':'Might be used by a PID/servo component.  The 0th order feed forward gain. This number is multiplied by the commanded position, resulting in a contribution to the computed voltage for the motor amplifier. The units on the FF0 gain are volts per machine unit'},
        'FF1':              { 'default':'0',         'help':'Might be used by a PID/servo component.  The 1st order feed forward gain. This number is multiplied by the change in commanded position per second, resulting in a contribution to the computed voltage for the motor amplifier. The units on the FF1 gain are volts per machine unit per second'},
        'FF2':              { 'default':'0',         'help':'Might be used by a PID/servo component.  The 2nd order feed forward gain. This number is multiplied by the change in commanded position per second per second, resulting in a contribution to the computed voltage for the motor amplifier. The units on the FF2 gain are volts per machine unit per second per second'},
        'OUTPUT_SCALE':     { 'default':'1.000',     'help':'Might be used by a PID/servo component.  These two values are the scale and offset factors for the axis output to the motor amplifiers. The second value (offset) is subtracted from the computed output (in volts), and divided by the first value (scale factor), before being written to the D/A converters. The units on the scale value are in true volts per DAC output volts. The units on the offset value are in volts. These can be used to linearize a DAC. '},
        'OUTPUT_OFFSET':    { 'default':'0.000',     'help':'Might be used by a PID/servo component.  These two values are the scale and offset factors for the axis output to the motor amplifiers. The second value (offset) is subtracted from the computed output (in volts), and divided by the first value (scale factor), before being written to the D/A converters. The units on the scale value are in true volts per DAC output volts. The units on the offset value are in volts. These can be used to linearize a DAC. '},
        'MAX_OUTPUT':       { 'default':'10',        'help':'Might be used by a PID/servo component.  The maximum value for the output of the PID compensation that is written to the motor amplifier, in volts. The computed output value is clamped to this limit. The limit is applied before scaling to raw output units. The value is applied symmetrically to both the plus and the minus side.'},
        'INPUT_SCALE':      { 'default':'20000',     'help':'Might be used by a PID/servo component.  '},
        'ENCODER_SCALE':    { 'default':'20000',     'help':'Might be used by a PID/servo component.  In PNCconf built configs Specifies the number of pulses that corresponds to a move of one machine unit as set in the [TRAJ] section. For a linear axis one machine unit will be equal to the setting of LINEAR_UNITS. For an angular axis one unit is equal to the setting in ANGULAR_UNITS. A second number, if specified, is ignored. '},
        'SCALE':            { 'default':'4000',      'help':'Might be used by a stepgen component.  Number of output step pulses for one unit of linear travel.'},
        'STEP_SCALE':       { 'default':'4000',      'help':'Might be used by a stepgen component. In PNCconf built configs Specifies the number of pulses that corresponds to a move of one machine unit as set in the [TRAJ] section. For stepper systems, this is the number of step pulses issued per machine unit. For a linear axis one machine unit will be equal to the setting of LINEAR_UNITS. For an angular axis one unit is equal to the setting in ANGULAR_UNITS. For servo systems, this is the number of feedback pulses per machine unit. A second number, if specified, is ignored.'},
        'ENCODER_SCALE':    { 'default':'',          'help':'Might be used by a stepgen component. (Optionally used in PNCconf built configs) - Specifies the number of pulses that corresponds to a move of one machine unit as set in the [TRAJ] section. For a linear axis one machine unit will be equal to the setting of LINEAR_UNITS. For an angular axis one unit is equal to the setting in ANGULAR_UNITS. A second number, if specified, is ignored.'},
        'STEPGEN_MAXACCEL': { 'default':'',          'help':'Might be used by a stepgen component.  Acceleration limit for the step generator. This should be 1% to 10% larger than the axis MAX_ACCELERATION. This value improves the tuning of stepgens "position loop". If you have added backlash compensation to an axis then this should be 1.5 to 2 times greater than MAX_ACCELERATION. '},
        'STEPGEN_MAXVEL':   { 'default':'',          'help':'Might be used by a stepgen component. Older configuration files have a velocity limit for the step generator as well. If specified, it should also be 1% to 10% larger than the axis MAX_VELOCITY. Subsequent testing has shown that use of STEPGEN_MAXVEL does not improve the tuning of stepgens position loop. '}
        },
     'EMC':{
        '':                 { 'default':'',          'help':'General LinuxCNC information'},
        'VERSION':          { 'default':'$Revision$','help':'The version number for the INI file. The default value looks odd because it is automatically updated when using the Revision Control System. Its a good idea to change this number each time you revise your file. If you want to edit this manually just change the number and leave the other tags alone.'},
        'MACHINE':          { 'default':'My Controller', 'help':'This is the name of the controller, which is printed out at the top of most graphical interfaces. You can put whatever you want here as long as you make it a single line long.'},
        'DEBUG':            { 'default':'0',         'help':'Debug level 0 means no messages will be printed when LinuxCNC is run from a terminal. Debug flags are usually only useful to developers. See src/emc/nml_intf/emcglb.h for other settings.'}
        },
    'DISPLAY':{
        '':                 { 'default':'',          'help':'Different user interface programs use different options, and not every option is supported by every user interface. The main two interfaces for LinuxCNC are AXIS and Touchy. Axis is an interface for use with normal computer and monitor, Touchy is for use with touch screens. Descriptions of the interfaces are in the Interfaces section of the User Manual.'},
        'DISPLAY':          { 'default':'axis',      'help':'The name of the user interface to use. Valid options may include: axis, touchy, keystick, mini, tklinuxcnc, xemc.'},
        'POSITION_OFFSET':  { 'default':'RELATIVE',  'help':'The coordinate system (RELATIVE or MACHINE) to show when the user interface starts. The RELATIVE coordinate system reflects the G92 and G5x coordinate offsets currently in effect'},
        'POSITION_FEEDBACK':{ 'default':'ACTUAL',    'help':'The coordinate value (COMMANDED or ACTUAL) to show when the user interface starts. The COMMANDED position is the ideal position requested by LinuxCNC. The ACTUAL position is the feedback position of the motors.'},
        'MAX_FEED_OVERRIDE':{ 'default':'1.2',       'help':'The maximum feed override the user may select. 1.2 means 120% of the programmed feed rate.'},
        'MIN_SPINDLE_OVERRIDE':{ 'default':'0.5',    'help':'The minimum spindle override the user may select. 0.5 means 50% of the programmed spindle speed. (This is useful as its dangerous to run a program with a too low spindle speed). '},
        'MAX_SPINDLE_OVERRIDE':{ 'default':'1.0',    'help':'The maximum spindle override the user may select. 1.0 means 100% of the programmed spindle speed.'},
        'PROGRAM_PREFIX':   { 'default':'~/emc2/nc_files', 'help':'The default location for g-code files and the location for user-defined M-codes. This location is searched for the file name before the subroutine path and user M path if specified in the [RS274NGC] section.'},
        'INTRO_GRAPHIC':    { 'default':'emc2.gif',  'help':'The image shown on the splash screen'},
        'INTRO_TIME':       { 'default':'5',         'help':'The maximum time to show the splash screen, in seconds. '},
        'CYCLE_TIME':       { 'default':'0.05',      'help':'Cycle time in seconds that display will sleep between polls. '},
        'DEFAULT_LINEAR_VELOCITY':{ 'default':'.25', 'help':'Applies to axis display only.  The default velocity for linear jogs, in machine units per second.'},
        'MIN_VELOCITY':     { 'default':'.01',       'help':'Applies to axis display only.  The approximate lowest value the jog slider.'},
        'MAX_LINEAR_VELOCITY':{ 'default':'1.0',     'help':'Applies to axis display only.  The maximum velocity for linear jogs, in machine units per second. '},
        'MIN_LINEAR_VELOCITY':{ 'default':'.01',     'help':'Applies to axis display only.  The approximate lowest value the jog slider. '},
        'DEFAULT_ANGULAR_VELOCITY':{ 'default':'.25','help':'Applies to axis display only.  The default velocity for angular jogs, in machine units per second. '},
        'MIN_ANGULAR_VELOCITY':{ 'default':'.01',    'help':'Applies to axis display only.  The approximate lowest value the jog slider. '},
        'MAX_ANGULAR_VELOCITY':{ 'default':'1.0',    'help':'Applies to axis display only.  The maximum velocity for angular jogs, in machine units per second. '},
        'INCREMENTS':       { 'default':'1 mm, .5 in, ...', 'help':'Applies to axis display only.  Defines the increments available for incremental jogs. The INCREMENTS can be used to override the default. The values can be decimal numbers (e.g., 0.1000) or fractional numbers (e.g., 1/16), optionally followed by a unit (cm, mm, um, inch, in or mil). If a unit is not specified the machine unit is assumed. Metric and imperial distances may be mixed: INCREMENTS = 1 inch, 1 mil, 1 cm, 1 mm, 1 um is a valid entry. '},
        'GRIDS':            { 'default':'10 mm, 1 in, ...',  'help':'Applies to axis display only.  Defines the preset values for grid lines. The value is interpreted the same way as INCREMENTS. '},
        'OPEN_FILE':        { 'default':'/full/path/to/file.ngc', 'help':'Applies to axis display only.  The file to show in the preview plot when AXIS starts. Use a blank string "" and no file will be loaded at start up. '},
        'EDITOR':           { 'default':'gedit',     'help':'Applies to axis display only.  The editor to use when selecting File > Edit to edit the gcode from the AXIS menu. This must be configured for this menu item to work. Another valid entry is gnome-terminal -e vim. '},
        'TOOL_EDITOR':      { 'default':'tooledit',  'help':'Applies to axis display only.  The editor to use when editing the tool table (for example by selecting "File > Edit tool table..." in Axis). Other valid entries are "gedit", "gnome-terminal -e vim", and "gvim".'},
        'PYVCP':            { 'default':'/filename.xml', 'help':'Applies to axis display only.  The PyVCP panel description file. See the PyVCP section for more information. '},
        'LATHE':            { 'default':'1',         'help':'Applies to axis display only.  This displays in lathe mode with a top view and with Radius and Diameter on the DRO. '},
        'GEOMETRY':         { 'default':'XYZABCUVW', 'help':'Applies to axis display only.  Controls the preview and backplot of rotary motion. This item consists of a sequence of axis letters, optionally preceded by a "-" sign. Only axes defined in [TRAJ]AXES should be used. This sequence specifies the order in which the effect of each axis is applied, with a "-" inverting the sense of the rotation. The proper GEOMETRY string depends on the machine configuration and the kinematics used to control it. The example string GEOMETRY=XYZBCUVW is for a 5-axis machine where kinematics causes UVW to move in the coordinate system of the tool and XYZ to move in the coordinate system of the material. The order of the letters is important, because it expresses the order in which the different transformations are applied. For example rotating around C then B is different than rotating around B then C. Geometry has no effect without a rotary axis. '},
        'ARCDIVISION':      { 'default':'64',        'help':'Applies to axis display only.  Set the quality of preview of arcs. Arcs are previewed by dividing them into a number of straight lines; a semicircle is divided into ARCDIVISION parts. Larger values give a more accurate preview, but take longer to load and result in a more sluggish display. Smaller values give a less accurate preview, but take less time to load and may result in a faster display. The default value of 64 means a circle of up to 3 inches will be displayed to within 1 mil (.03%).'},
        'MDI_HISTORY_FILE': { 'default':'',          'help':'Applies to axis display only.  The name of a local MDI history file. If this is not specified Axis will save the MDI history in .axis_mdi_history in the users home directory. This is useful if you have multiple configurations on one computer. '},
        'HELP_FILE':     { 'default':'tklinucnc.txt',       'help':'Applies to TKLinuxCNC display only.  Path to help file.'}
        },
    'FILTER':{
        '':                 { 'default':'',          'help':'AXIS has the ability to send loaded files through a filter program. This filter can do any desired task: Something as simple as making sure the file ends with M2, or something as complicated as detecting whether the input is a depth image, and generating g-code to mill the shape it defines. The [FILTER] section of the ini file controls how filters work. First, for each type of file, write a PROGRAM_EXTENSION line. Then, specify the program to execute for each type of file. This program is given the name of the input file as its first argument, and must write RS274NGC code to standard output. This output is what will be displayed in the text area, previewed in the display area, and executed by LinuxCNC when Run.'},
        'PROGRAM_EXTENSION':{ 'default':'.extension Description', 'help':'Example: The following lines add support for the image-to-gcode converter included with LinuxCNC: PROGRAM_EXTENSION = .png,.gif,.jpg Greyscale Depth Image, then png = image-to-gcode, gif = image-to-gcode, jpg = image-to-gcode'}
        },
    'RS274NGC':{
        '':                 { 'default':'',          'help':''},
        'PARAMETER_FILE':   { 'default':'myfile.var','help':'The file located in the same directory as the ini file which contains the parameters used by the interpreter (saved between runs).'},
        'ORIENT_OFFSET':    { 'default':'0',         'help':'A float value added to the R word parameter of an M19 Orient Spindle operation. Used to define an arbitrary zero position regardless of encoder mount orientation. '},
        'RS274NGC_STARTUP_CODE': { 'default':'G01 G17 G20 G40 G49 G64 P0.001 G80 G90 G92 G94 G97 G98', 'help':'A string of NC codes that the interpreter is initialized with. This is not a substitute for specifying modal g-codes at the top of each ngc file, because the modal codes of machines differ, and may be changed by g-code interpreted earlier in the session. '},
        'SUBROUTINE_PATH':  { 'default':'ncsubroutines:/tmp/testsubs:lathesubs:millsubs', 'help':'Specifies a colon (:) separated list of up to 10 directories to be searched when single-file subroutines are specified in gcode. These directories are searched after searching [DISPLAY]PROGRAM_PREFIX (if it is specified) and before searching [WIZARD]WIZARD_ROOT (if specified). The paths are searched in the order that they are listed. The first matching subroutine file found in the search is used. Directories are specified relative to the current directory for the inifile or as absolute paths. The list must contain no intervening whitespace. '},
        'USER_M_PATH':      { 'default':'myfuncs:/tmp/mcodes:experimentalmcodes', 'help':'Specifies a list of colon (:) separated directories for user defined functions. Directories are specified relative to the current directory for the inifile or as absolute paths. The list must contain no intervening whitespace. '},
        'USER_DEFINED_FUNCTION_MAX_DIRS': { 'default':'5', 'help':'The maximum number of directories defined at compile time'}
        },
    'EMCMOT':{
        '':                 { 'default':'',          'help':'This section is a custom section and is not used by LinuxCNC directly. Most configurations use values from this section to load the motion controller.'},
        'EMCMOT':           { 'default':'motmod',    'help':'The motion controller name is typically used here. '},
        'BASE_PERIOD':      { 'default':'50000',     'help':'The Base task period in nanoseconds.'},
        'SERVO_PERIOD':     { 'default':'1000000',   'help':'This is the "Servo" task period in nanoseconds. '},
        'TRAJ_PERIOD':      { 'default':'100000',    'help':'This is the Trajectory Planner task period in nanoseconds.'}
        },
    'TASK':{
        '':                 { 'default':'',          'help':''},
        'TASK':             { 'default':'milltask',  'help':'Specifies the name of the task executable. The task executable does various things, such as communicate with the UIs over NML, communicate with the realtime motion planner over non-HAL shared memory, and interpret gcode. Currently there is only one task executable that makes sense for 99.9% of users, milltask.'},
        'CYCLE_TIME':       { 'default':'0.010',     'help':'The period, in seconds, at which TASK will run. This parameter affects the polling interval when waiting for motion to complete, when executing a pause instruction, and when accepting a command from a user interface. There is usually no need to change this number.'}
        },
    'HAL':{
        '':                 { 'default':'',          'help':''},
        'TWOPASS':          { 'default':'ON',        'help':'Use two pass processing for loading HAL comps. With TWOPASS processing, all [HAL]HALFILES are first read and multiple appearances of loadrt directives for each module are accumulated. No hal commands are executed in this initial pass. '},
        'HALFILE':          { 'default':'example.hal', 'help':'Execute the file example.hal at start up. If HALFILE is specified multiple times, the files are executed in the order they appear in the ini file. Almost all configurations will have at least one HALFILE, and stepper systems typically have two such files, one which specifies the generic stepper configuration (core_stepper.hal) and one which specifies the machine pin out (xxx_pinout.hal) '},
        'HALCMD':           { 'default':'command',   'help':'Execute command as a single HAL command. If HALCMD is specified multiple times, the commands are executed in the order they appear in the ini file. HALCMD lines are executed after all HALFILE lines. '},
        'SHUTDOWN':         { 'default':'shutdown.hal', 'help':'Execute the file shutdown.hal when LinuxCNC is exiting. Depending on the hardware drivers used, this may make it possible to set outputs to defined values when LinuxCNC is exited normally. However, because there is no guarantee this file will be executed (for instance, in the case of a computer crash) it is not a replacement for a proper physical e-stop chain or other protections against software failure. '},
        'POSTGUI_HALFILE':  { 'default':'example2.hal', 'help':'(Only with the TOUCHY and AXIS GUI) Execute example2.hal after the GUI has created its HAL pins. '},
        'HALUI':            { 'default':'halui',     'help':'Adds the HAL user interface pins. '}
        },
    'HALUI':{
        '':                 { 'default':'',          'help':''},
        'MDI_COMMAND':      { 'default':'G53 G0 X0 Y0 Z0', 'help':' An MDI command can be executed by using halui.mdi-command-00. Increment the number for each command listed in the [HALUI] section. '}
        },
    'TRAJ':{
        '':                 { 'default':'',          'help':'The [TRAJ] section contains general parameters for the trajectory planning module in motion.'},
        'COORDINATES':      { 'default':'X Y Z',     'help':'The names of the axes being controlled. Only X, Y, Z, A, B, C, U, V, W are valid. Only axes named in COORDINATES are accepted in g-code. This has no effect on the mapping from G-code axis names (X- Y- Z-) to joint numbers for trivial kinematics, X is always joint 0, A is always joint 3, and U is always joint 6, and so on. It is permitted to write an axis name twice (e.g., X Y Y Z for a gantry machine) but this has no effect. '},
        'AXES':             { 'default':'3',         'help':'One more than the number of the highest joint number in the system. For an XYZ machine, the joints are numbered 0, 1 and 2; in this case AXES should be 3. For an XYUV machine using trivial kinematics, the V joint is numbered 7 and therefore AXES should be 8. For a machine with nontrivial kinematics (e.g., scarakins) this will generally be the number of controlled joints. '},
        'JOINTS':           { 'default':'3',         'help':'(This config variable is used by the Axis GUI only, not by the trajectory planner in the motion controller.) Specifies the number of joints (motors) in the system. For example, an XYZ machine with a single motor for each axis has 3 joints. A gantry machine with one motor on each of two of the axes, and two motors on the third axis, has 4 joints. '},
        'HOME':             { 'default':'0 0 0',     'help':'Coordinates of the homed position of each axis. Again for a fourth axis you will need 0 0 0 0. This value is only used for machines with nontrivial kinematics. On machines with trivial kinematics this value is ignored. '},
        'LINEAR_UNITS':     { 'default':'<units>',   'help':'Specifies the machine units for linear axes. Possible choices are (in, inch, imperial, metric, mm). This does not affect the linear units in NC code (the G20 and G21 words do this). '},
        'ANGULAR_UNITS':    { 'default':'<units>',   'help':'Specifies the machine units for rotational axes. Possible choices are deg, degree (360 per circle), rad, radian (2pi per circle), grad, or gon (400 per circle). This does not affect the angular units of NC code. In RS274NGC, A-, B- and C- words are always expressed in degrees.'},
        'DEFAULT_VELOCITY': { 'default':'0.0167',    'help':'The initial rate for jogs of linear axes, in machine units per second. The value shown in Axis equals machine units per minute. '},
        'DEFAULT_ACCELERATION': { 'default':'2.0',   'help':'In machines with nontrivial kinematics, the acceleration used for "teleop" (Cartesian space) jogs, in machine units per second per second. '},
        'MAX_VELOCITY':     { 'default':'5.0',       'help':'The maximum velocity for any axis or coordinated move, in machine units per second. The value shown equals 300 units per minute. '},
        'MAX_ACCELERATION': { 'default':'20.0',      'help':'The maximum acceleration for any axis or coordinated axis move, in machine units per second per second. '},
        'POSITION_FILE':    { 'default':'position.txt', 'help':'If set to a non-empty value, the joint positions are stored between runs in this file. This allows the machine to start with the same coordinates it had on shutdown. This assumes there was no movement of the machine while powered off. If unset, joint positions are not stored and will begin at 0 each time LinuxCNC is started. This can help on smaller machines without home switches. '},
        'NO_FORCE_HOMING':  { 'default':'1',          'help':'The default behavior is for LinuxCNC to force the user to home the machine before any MDI command or a program is run. Normally, only jogging is allowed before homing. Setting NO_FORCE_HOMING = 1 allows the user to make MDI moves and run programs without homing the machine first. Interfaces without homing ability will need to have this option set to 1. '}
        },
    'EMCIO':{        
        '':                 { 'default':'',          'help':'Tool changeer related information.'},
        'EMCIO':            { 'default':'io',        'help':'Name of IO controller program, e.g., io'},
        'CYCLE_TIME':       { 'default':'0.100',     'help':'The period, in seconds, at which EMCIO will run. Making it 0.0 or a negative number will tell EMCIO not to sleep at all. There is usually no need to change this number. '},
        'TOOL_TABLE':       { 'default':'tool.tbl',  'help':'The file which contains tool information, described in the User Manual. '},
        'TOOL_CHANGE_POSITION': { 'default':'0 0 2', 'help':'Specifies the XYZ location to move to when performing a tool change if three digits are used. Specifies the XYZABC location when 6 digits are used. Specifies the XYZABCUVW location when 9 digits are used. Tool Changes can be combined. For example if you combine the quill up with change position you can move the Z first then the X and Y. '},
        'TOOL_CHANGE_WITH_SPINDLE_ON': { 'default':'1', 'help':'The spindle will be left on during the tool change when the value is 1. Useful for lathes or machines where the material is in the spindle, not the tool. '},
        'TOOL_CHANGE_QUILL_UP': { 'default':'1',     'help':'The Z axis will be moved to machine zero prior to the tool change when the value is 1. This is the same as issuing a G0 G53 Z0. '},
        'TOOL_CHANGE_AT_G30':   { 'default':'1',     'help':'The machine is moved to reference point defined by parameters 5181-5186 for G30 if the value is 1. For more information on G30 and Parameters see the G Code Manual. '},
        'RANDOM_TOOLCHANGER':   { 'default':'1',     'help':'This is for machines that cannot place the tool back into the pocket it came from. For example, machines that exchange the tool in the active pocket with the tool in the spindle. '}
        }
    }
ConfigHelp['AXIS_1'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_2'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_3'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_4'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_5'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_6'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_7'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_8'] = ConfigHelp['AXIS_0'];
ConfigHelp['AXIS_9'] = ConfigHelp['AXIS_0'];
    

# *****************************************************
# HAL Interface
#
# Puts pins on this python module for interaction with
# the HAL.


# PROBLEM:  it works if you load it
# once, but if linuxcnc goes down and restarts, this
# needs to re-set the HAL pins in the new linuxcnc instance
# *****************************************************
class HALInterface( object ):
    def __init__(self):
        self.h = None
        return  #****************************************************************** This section omitted for now during debugging.  Re-enable later.
        try:
            self.h = hal.component("LinuxCNCWebSktSvr")

            # create hal pins
            self.h.newpin("keepalive_counter", hal.HAL_U32, hal.HAL_OUT)
            self.h.newpin("time_since_keepalive", hal.HAL_FLOAT, hal.HAL_OUT)
            self.h['keepalive_counter'] = 0
            self.h['time_since_keepalive'] = 0
            self.keepalive_counter = 0
            self.time_of_last_keepalive = time.time()
            self.time_elapsed = 0

            # begin the poll-update loop of the linuxcnc system
            self.scheduler = tornado.ioloop.PeriodicCallback( self.poll_update, UpdateHALOutputsPollPeriodInMilliSeconds, io_loop=main_loop )
            self.scheduler.start()

            self.h.ready()
        except:
            self.h = None
            print "WARNING: NO HAL PIN INTERFACE.  HAL Pin creation failed."
            logging.warn("WARNING: NO HAL PIN INTERFACE.  HAL Pin creation failed.")
        
    def Tick( self ):
        if ( self.h is not None ):
            self.keepalive_counter = self.keepalive_counter + 1
            self.h['keepalive_counter'] = self.keepalive_counter
            previous_time = self.time_of_last_keepalive
            self.time_of_last_keepalive = time.time()
            self.time_elapsed = self.time_of_last_keepalive - previous_time
            self.h['time_since_keepalive'] = self.time_elapsed

    def poll_update( self ):
        if ( self.h is not None ):
            previous_time = self.time_of_last_keepalive
            now_time = time.time()
            self.time_elapsed = now_time - previous_time
            self.h['time_since_keepalive'] = self.time_elapsed

HAL_INTERFACE = HALInterface()        

# Config File Editor
INIFileDataTemplate = {
    "parameters":[],
    "sections":{}
    }


# *****************************************************
# Process a command sent from the client
# commands come in as json objects, and are converted to dict python objects
# *****************************************************
class LinuxCNCServerCommand( object ):

    # Error codes
    REPLY_NAK = '?ERR'
    REPLY_STATUS_NOT_FOUND = '?Status Item Not Found'
    REPLY_INVALID_COMMAND = '?Invalid Command'
    REPLY_INVALID_COMMAND_PARAMETER = '?Invalid Parameter'
    REPLY_ERROR_EXECUTING_COMMAND = '?Error executing command'
    REPLY_MISSING_COMMAND_PARAMETER = '?Missing Parameter'
    REPLY_LINUXCNC_NOT_RUNNING = '?LinuxCNC is not running'
    REPLY_COMMAND_OK = '?OK'
    REPLY_INVALID_USERID = '?Invalid User ID'

    def __init__( self, statusItems, commandItems, server_command_handler, status_poller, command_message='{"command": "invalid"}', command_dict=None ):
        self.linuxcnc_status_poller = status_poller
        self.command_message = command_message
        self.StatusItems = statusItems
        self.CommandItems = commandItems
        self.server_command_handler = server_command_handler
        self.async_reply_buf = []
        self.async_reply_buf_lock = threading.Lock() 
        
        if (command_dict is None):        
            try:
                self.commandDict = json.loads( command_message )
                self.command = self.commandDict['command'].strip()
            except:
                self.commandDict = {'command': 'invalid'}
                self.command = 'invalid'
        else:
            self.commandDict = command_dict
            self.command = command_dict.get('command','invalid')

    # Convert self.replyval into a JSON string suitable to return to the command originator
    def form_reply( self ):
        self.replyval['id'] = self.commandID
        if ( 'code' not in self.replyval ):
            self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK
        if ('data' not in self.replyval):
                self.replyval['data'] = self.replyval['code']
        val = json.dumps( self.replyval, cls=StatusItemEncoder )
        return val

    # update on a watched variable 
    def on_new_poll( self ):
        try:
            if (not self.statusitem.watchable):
                self.linuxcnc_status_poller.del_observer( self.on_new_poll )
                return
            if self.server_command_handler.isclosed:
                self.linuxcnc_status_poller.del_observer( self.on_new_poll )
                return
            newval = self.statusitem.get_cur_status_value(self.linuxcnc_status_poller, self.item_index, self.commandDict )
            if (self.replyval['data'] != newval['data']):
                self.replyval = newval
                self.server_command_handler.send_message( self.form_reply() )
                if ( newval['code'] != LinuxCNCServerCommand.REPLY_COMMAND_OK ):
                    self.linuxcnc_status_poller.del_observer( self.on_new_poll )
        except:
            pass

    def monitor_async(self):
        if (len(self.async_reply_buf) > 0):
            
            self.async_reply_buf_lock.acquire()

            self.replyval = self.async_reply_buf[0]         
            self.server_command_handler.send_message( self.form_reply() )
            self.async_reply_buf_lock.release()

            self.linuxcnc_status_poller.del_observer( self.monitor_async )
        
        return

    # this is the main interface to a LinuxCNCServerCommand.  This determines what the command is, and executes it.
    # Callbacks are made to the self.server_command_handler to write output to the websocket
    # The self.linuxcnc_status_poller is used to poll the linuxcnc status, which is used to watch status items and monitor for changes
    def execute( self ):
        self.commandID = self.commandDict.get('id','none')
        self.replyval = {}
        self.replyval['code'] = LinuxCNCServerCommand.REPLY_INVALID_COMMAND
        if ( self.command == 'get'):
            try:
                self.item_index = 0
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER
                self.statusItemName = self.commandDict['name'].strip()
                self.statusitem = StatusItem.from_name( self.statusItemName )
                if (self.statusitem is None):
                    self.replyval['code'] = LinuxCNCServerCommand.REPLY_STATUS_NOT_FOUND
                else:
                    if ( self.statusitem.isarray ):
                        self.item_index = self.commandDict['index']
                        self.replyval['index'] = self.item_index;

                    if (self.statusitem.isasync):
                        self.linuxcnc_status_poller.add_observer( self.monitor_async )
                        
                    self.replyval = self.statusitem.get_cur_status_value(self.linuxcnc_status_poller, self.item_index, self.commandDict, async_buffer=self.async_reply_buf, async_lock=self.async_reply_buf_lock )
            except:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK

        elif (self.command == 'watch'):
            try:
                self.item_index = 0
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER
                self.statusItemName = self.commandDict['name'].strip()
                self.statusitem = StatusItem.from_name( self.statusItemName )
                if (self.statusitem is None):
                    self.replyval['code'] = LinuxCNCServerCommand.REPLY_STATUS_NOT_FOUND
                else:
                    if ( self.statusitem.isarray ):
                        self.item_index = self.commandDict['index']
                        self.replyval['index'] = self.item_index;
                    self.replyval = self.statusitem.get_cur_status_value(self.linuxcnc_status_poller, self.item_index, self.commandDict )
                    if (self.replyval['code'] == LinuxCNCServerCommand.REPLY_COMMAND_OK ):
                        self.linuxcnc_status_poller.add_observer( self.on_new_poll )
            except:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK
            

        elif (self.command == 'list_get'):
            try:
                self.replyval['data'] = StatusItems.values()
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
            except:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK

        elif (self.command == 'list_put'):
            try:
                self.replyval['data'] = CommandItems.values()
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
            except:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK

        elif (self.command == 'put'):
            self.replyval['code'] = LinuxCNCServerCommand.REPLY_NAK
            try:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_INVALID_COMMAND_PARAMETER
                self.LinuxCNCCommandName = self.commandDict['name']
                self.commanditem = self.CommandItems.get( self.LinuxCNCCommandName )
                self.replyval = self.commanditem.execute( self.commandDict, self.linuxcnc_status_poller )
            except:
                logging.debug( 'PUT Command: ERROR'  )
                
 
        elif (self.command == 'keepalive'):
            global HAL_INTERFACE
            try:
                HAL_INTERFACE.Tick()
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_COMMAND_OK
                self.replyval['counter'] = HAL_INTERFACE.keepalive_counter
                self.replyval['elapsed_time'] = HAL_INTERFACE.time_elapsed
            except:
                self.replyval['code'] = LinuxCNCServerCommand.REPLY_ERROR_EXECUTING_COMMAND
            
        # convert to JSON, and return the reply string
        return self.form_reply()





# *****************************************************
# *****************************************************
class LinuxCNCCommandWebSocketHandler(tornado.websocket.WebSocketHandler):

    def __init__(self, *args, **kwargs):
        global LINUXCNCSTATUS
        super( LinuxCNCCommandWebSocketHandler, self ).__init__( *args, **kwargs )
        self.user_validated = False
        print "New websocket Connection..."
    
    def open(self,arg):
        global LINUXCNCSTATUS
        self.isclosed = False
        self.stream.socket.setsockopt( socket.IPPROTO_TCP, socket.TCP_NODELAY, 1 )

    def allow_draft76(self):
        return False    

    def on_message(self, message): 
        global LINUXCNCSTATUS
        if int(options.verbose) > 2:
            if (message.find("\"HB\"") < 0):
                print "GOT: " + message
        if (self.user_validated):
            try:
                reply = LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_message=message ).execute()
                self.write_message(reply)
                if int(options.verbose) > 3:
                    if (reply.find("\"HB\"") < 0) and (reply.find("backplot") < 0):
                        print "Reply: " + reply
            except Exception as ex:
                print "1:", ex
        else:
            try: 
                global userdict
                commandDict = json.loads( message )
                id = commandDict.get('id','Login').strip()
                user = commandDict['user'].strip()
                pw = hashlib.md5(commandDict['password'].strip()).hexdigest()
                if ( ( user in userdict ) and ( userdict.get(user) == pw ) ):
                    self.user_validated = True
                    self.write_message(json.dumps( { 'id':id, 'code':'?OK', 'data':'?OK'}, cls=StatusItemEncoder ))
                    if int(options.verbose) > 2:
                        print "Logged in " + user
                else:
                    self.write_message(json.dumps( { 'id':id, 'code':'?User not logged in', 'data':'?User not logged in'}, cls=StatusItemEncoder ))
                    if int(options.verbose) > 2:
                        print "Logged FAILED " + user
            except:
                if int(options.verbose) > 2:
                    print "Logged FAILED (user unknown)"
                self.write_message(json.dumps( { 'id':id, 'code':'?User not logged in', 'data':'?User not logged in'}, cls=StatusItemEncoder ))

            
 
    def send_message( self, message_to_send ):
        self.write_message( message_to_send )
        if int(options.verbose) > 4:
            if (message_to_send.find("actual_position") < 0) and (message_to_send.find("\"HB\"") < 0) and (message_to_send.find("backplot") < 0) :
                print "SEND: " + message_to_send

    def on_close(self):
        self.isclosed = True
        logging.debug( "WebSocket closed" )

    def select_subprotocol(self, subprotocols):
        if ('linuxcnc' in subprotocols ):
            return 'linuxcnc'
        elif (subprotocols == ['']): # some websocket clients don't support subprotocols, so allow this if they just provide an empty string
            return '' 
        else:
            logging.warning('WEBSOCKET CLOSED: sub protocol linuxcnc not supported')
            logging.warning( 'Subprotocols: ' + subprotocols.__str__() )
            self.close()
            return None


def check_user( user, pw ):
    # check if the user/pw combo is in our dictionary
    user = user.strip()
    pw = hashlib.md5(pw.strip()).hexdigest()
    global userdict
    if ( ( user in userdict ) and ( userdict.get(user) == pw ) ):
        return True
    else:
        return False

# *****************************************************
# *****************************************************
# A decorator that lets you require HTTP basic authentication from visitors.
#
# Kevin Kelley <kelleyk@kelleyk.net> 2011
# Use however makes you happy, but if it breaks, you get to keep both pieces.
# Post with explanation, commentary, etc.:
# http://kelleyk.com/post/7362319243/easy-basic-http-authentication-with-tornado
# Usage example:
#@require_basic_auth
#class MainHandler(tornado.web.RequestHandler):
# def get(self, basicauth_user, basicauth_pass):
# self.write('Hi there, {0}! Your password is {1}.' \
# .format(basicauth_user, basicauth_pass))
# def post(self, **kwargs):
# basicauth_user = kwargs['basicauth_user']
# basicauth_pass = kwargs['basicauth_pass']
# self.write('Hi there, {0}! Your password is {1}.' \
# .format(basicauth_user, basicauth_pass))
# *****************************************************
# *****************************************************
def require_basic_auth(handler_class):
    def wrap_execute(handler_execute):
        def require_basic_auth(handler, kwargs):
            auth_header = handler.request.headers.get('Authorization')
            if auth_header is None or not auth_header.startswith('Basic '):
                handler.set_status(401)
                handler.set_header('WWW-Authenticate', 'Basic realm=Restricted')
                handler._transforms = []
                handler.finish()
                print "Authorization Challenge - login failed."
                return False
            auth_decoded = base64.decodestring(auth_header[6:])
            user, pw = auth_decoded.split(':', 2)

            # check if the user/pw combo is in our dictionary
            return check_user( user, pw )
        
        def _execute(self, transforms, *args, **kwargs):
            if not require_basic_auth(self, kwargs):
                return False
            return handler_execute(self, transforms, *args, **kwargs)
        return _execute

    handler_class._execute = wrap_execute(handler_class._execute)
    return handler_class



# *****************************************************
@require_basic_auth
class PollHandler(tornado.web.RequestHandler):
    def get(self, arg):

        # if this request is sending a callback, then assume jsonp for return type
        jsonp = self.get_argument("callback", None)
        if (jsonp is None):
            jsonp = self.get_argument("jsonp", None)
        
        args = arg.split("/")
        args = [tornado.escape.url_unescape(x) for x in args]
        command_dict = {'command':args[0]}
        for idx in range(1,len(args),2):
            try:
                val = args[idx+1]
                # try and convert anything that is a number to an actual number (not a string)
                # use int formatting if possible, otherwise use float
                v1 = float(val)
                v2 = int(val)
                if (v1 == v2):
                    val = v2
                else:
                    val = v1
            except:
                pass
            command_dict[args[idx]] = val

        self.set_header("Access-Control-Allow-Origin","*")
        if (jsonp is not None):
            self.set_header("Content-Type", "application/javascript")
            self.write( jsonp + '(' +  LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_dict=command_dict ).execute() + ')' )
        else:
            self.set_header("Content-Type", "application/json")
            self.write(LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_dict=command_dict ).execute())
        self.finish()


# *****************************************************  
@require_basic_auth
class PollHandlerJSON(tornado.web.RequestHandler):
    def get(self, arg):
        
        arg = tornado.escape.url_unescape(arg)
        jsonp = self.get_argument("callback", None)
        if (jsonp is None):
            jsonp = self.get_argument("jsonp", None)

        self.set_header("Access-Control-Allow-Origin","*")            
        if (jsonp is not None):
            self.set_header("Content-Type", "application/javascript")
            self.write( jsonp + '(' + LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_message=arg ).execute() + ')' )
        else:
            self.set_header("Content-Type", "application/json")
            self.write(LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_message=arg ).execute())
        self.finish()
  
# *****************************************************
class PollHeaderLogin(tornado.web.RequestHandler):
    def get(self, arg):
        self.write( json.dumps({'code':'?OK'}) )
        self.finish()
        return

        login = False
        if "user" in self.request.headers:
            if "pw" in self.request.headers:
                if check_user( self.request.headers["user"], self.request.headers["pw"] ):
                    login = True
        if not login:
            print "Login Failed in query."
            self.set_header("Content-Type", "application/json")
            self.write( json.dumps({'code':'?Invalid User ID'}) )
            self.finish()
            return

        command_dict = {}
        for k in self.request.arguments:
            try:
                val = self.get_argument(k)
                # try and convert anything that is a number to an actual number (not a string)
                # use int formatting if possible, otherwise use float
                v1 = float(val)
                v2 = int(val)
                if (v1 == v2):
                    val = v2
                else:
                    val = v1
            except:
                pass
            command_dict[k] = val

        jsonp = self.get_argument("callback", None)
        if (jsonp is None):
            jsonp = self.get_argument("jsonp", None)

        self.set_header("Access-Control-Allow-Origin","*")    
        if (jsonp is not None):
            self.set_header("Content-Type", "application/javascript")
            self.write( jsonp + '(' + LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_dict=command_dict ).execute() + ')' )
        else:
            self.set_header("Content-Type", "application/json")
            self.write(LinuxCNCServerCommand( StatusItems, CommandItems, self, LINUXCNCSTATUS, command_dict=command_dict ).execute())
            
        self.finish()

def readUserList():
    global userdict
    global application_path

    logging.info("Reading user list...")
    userdict = {}
    try:
        parser = SafeConfigParser() 
        parser.read(os.path.join(application_path,'users.ini'))
        for name, value in parser.items('users'):
            userdict[name] = value
    except Exception as ex:
        print "Error reading users.ini:", ex

# *****************************************************
# *****************************************************
class MainHandler(tornado.web.RequestHandler):
    def get(self, arg):
        if (arg.upper() in [ '', 'INDEX.HTML', 'INDEX.HTM', 'INDEX']):
            self.render( 'LinuxCNCConfig.html' )
        else:
            self.render( arg ) 

# ********************************
# ********************************
#  Initialize global variables
# ********************************
# ********************************

# determine current path to executable
# determine if application is a script file or frozen exe
global application_path
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
elif __file__:
    application_path = os.path.dirname(__file__)

# The main application object:
# the /command/ and /polljason/ use HTTP Basic Authorization to log in.
# the /pollhl/ use HTTP header arguments to log in
application = tornado.web.Application([
    (r"/([^\\/]*)", MainHandler, {} ),
    (r"/command/(.*)", PollHandler, {} ),  
    (r"/polljson/(.*)", PollHandlerJSON, {} ),
    (r"/query/(.*)", PollHeaderLogin, {} ),
    (r"/websocket/(.*)", LinuxCNCCommandWebSocketHandler, {} ),
    ],
    debug=True,
    template_path=os.path.join(application_path, "templates"),
    static_path=os.path.join(application_path, "static"),
    )

# ********************************
# ********************************
# main()
# ********************************
# ******************************** 
def main():
    global INI_FILENAME
    global INI_FILE_PATH
    global userdict
    global instance_number
    global LINUXCNCSTATUS
    global options
    global userdict

    def fn():
        instance_number = random()
        print "Webserver reloading..."

    parser = OptionParser()
    parser.add_option("-v", "--verbose", dest="verbose", default=0,
                      help="Verbosity level.  Default to 0 for quiet.  Set to 5 for max.")

    (options, args) = parser.parse_args()

    if ( int(options.verbose) > 4):
        print "Options: ", options
        print "Arguments: ", args[0]

    instance_number = random()
    LINUXCNCSTATUS = LinuxCNCStatusPoller(main_loop, UpdateStatusPollPeriodInMilliSeconds)

    if ( int(options.verbose) > 4):
        print "Parsing INI File Name"

    if len(args) < 1:
        sys.exit('Usage: LinuxCNCWebSktSvr.py <LinuxCNC_INI_file_name>')
    INI_FILENAME = args[0]
    [INI_FILE_PATH, x] = os.path.split( INI_FILENAME )

    if ( int(options.verbose) > 4):
        print "INI File: ", INI_FILENAME


    logging.basicConfig(filename=os.path.join(application_path,'linuxcnc_webserver.log'),format='%(asctime)sZ pid:%(process)s module:%(module)s %(message)s', level=logging.ERROR)
 
    #rpdb2.start_embedded_debugger("password")

    readUserList()

    logging.info("Starting linuxcnc http server...")
    print "Starting Rockhopper linuxcnc http server."

    # see http://www.akadia.com/services/ssh_test_certificate.html to learn how to generate a new server SSL certificate
    # for httpS protocol:
    #application.listen(8000, ssl_options=dict(
    #    certfile="server.crt",
    #    keyfile="server.key",
    #    ca_certs="/etc/ssl/certs/ca-certificates.crt",
    #    cert_reqs=ssl.CERT_NONE) )

    # for non-httpS (plain old http):
    application.listen(8000)

    # cause tornado to restart if we edit this file.  Usefull for debugging
    tornado.autoreload.add_reload_hook(fn)
    tornado.autoreload.start()

    # start up the webserver loop
    main_loop.start() 

# auto start if executed from the command line
if __name__ == "__main__":

    try:
        main()
    except Exception as ex:
        print ex
            

