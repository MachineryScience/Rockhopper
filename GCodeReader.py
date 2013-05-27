#
# Copyright 2012, 2013 Machinery Science, LLC
#
import rs274.glcanon
import rs274.interpret
import linuxcnc
import gcode

import time

import tempfile
import shutil
import os
import sys
import json

import xml.etree.ElementTree as xml

class NullProgress:
    def nextphase(self, var1): pass
    def progress(self): pass

class StatCanon(rs274.glcanon.GLCanon, rs274.interpret.StatMixin):
    def __init__(self, colors, geometry, lathe_view_option, stat, random):
        rs274.glcanon.GLCanon.__init__(self, colors, geometry)
        rs274.interpret.StatMixin.__init__(self, stat, random)
        self.progress = NullProgress()
        self.lathe_view_option = lathe_view_option
    def is_lathe(self): return self.lathe_view_option



class GCodeRender( rs274.glcanon.GlCanonDraw ):
    def __init__(self, inifile):
        self.inifile = linuxcnc.ini(inifile)
        [self.INI_FILE_PATH, x] = os.path.split( inifile )
        self.select_primed = None

        temp = self.inifile.find("DISPLAY", "LATHE")
        self.lathe_option = bool(temp == "1" or temp == "True" or temp == "true" )

        rs274.glcanon.GlCanonDraw.__init__(self, linuxcnc.stat(), None)
	live_axis_count = 0
	for i,j in enumerate("XYZABCUVW"):
	    if self.stat.axis_mask & (1<<i) == 0: continue
	    live_axis_count += 1
	self.num_joints = int(self.inifile.find("TRAJ", "JOINTS") or live_axis_count)
    
    # load a g-code file, simulate it
    def load(self,filename=None):

        # load the filename from linuxcnc, or used the passed in file name if none is already loaded
        s = self.stat
        s.poll()
        if not filename and s.file:
            filename = s.file
        elif not filename and not s.file:
            return

        self._current_file = filename
        try:
            # indicate the style of tool-changer 
            random = int(self.inifile.find("EMCIO", "RANDOM_TOOLCHANGER") or 0)
            # create the object which handles the canonical motion callbacks (straight_feed, straight_traverse, arc_feed, rigid_tap, etc.)
            # StatCanon inherits from GLCanon, which will do the work for us here
            self.canon = StatCanon(None, self.get_geometry(),self.lathe_option, s, random)

            # load numbered g-code variables from files.  Current working directory must be where the files live
            # Parameter files are persistent accross linuxcnc sessions.  Since this is just a simulation, we don't want
            # the gcode file to actually change the persistent parameters, so we make a temporary copy
            parameter = self.inifile.find("RS274NGC", "PARAMETER_FILE")
            temp_parameter_orig = os.path.join(self.INI_FILE_PATH, os.path.basename(parameter or "linuxcnc.var"))
            temp_parameter_new = os.path.join( os.getcwd(), "tmp_params.var" )
            if os.path.exists(parameter):
                shutil.copy(temp_parameter_orig, temp_parameter_new )
            self.canon.parameter_file = temp_parameter_new

            # Some initialization g-code to set the units and optional user code
            unitcode = "G%d" % (20 + (s.linear_units == 1))
            initcode = self.inifile.find("RS274NGC", "RS274NGC_STARTUP_CODE") or ""

            # THIS IS WHERE IT ALL HAPPENS: load_preview will execute the code, call back to the canon with motion commands, and
            # record a history of all the movements.   
            result, seq = self.load_preview(filename, self.canon, unitcode, initcode)
	    if result > gcode.MIN_ERROR:
		self.report_gcode_error(result, seq, filename)

        finally:
            pass

    def write_json( self, filename, compact=True, fixed_point_precision=5, maxlines=-1 ):
        try:
            f = open(filename,'w')
            f.write( self.to_json(compact=compact, fixed_point_precision=fixed_point_precision, maxlines=maxlines) )
        finally:
            f.close()

    def to_json( self, compact=True, fixed_point_precision=5, maxlines=-1 ):
        # each item is:
        # 1) Line number (the line number in the gcode that generated this movement
        # 2) a tuple of coordinates: the line start location
        # 3) a tuple of coordinates: the line end location
        # 4) feedrate (ONLY FOR "FEED" and "ARCFEED" entries)
        # 4) a tuple of coordinates: the tool offset
        obj = {}

        mult = pow(10,fixed_point_precision)
        linecount = 0
        
        # reduce size by limiting to 3 axes, and 4 digits of precision
        if (compact):
            obj['feed'] = []
            for item in self.canon.feed:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['feed'].append( [ item[0], [ int(round(mult*item[1][0])), int(round(mult*item[1][1])), int(round(mult*item[1][2])) ], [ int(round(mult*item[2][0])), int(round(mult*item[2][1])), int(round(mult*item[2][2])) ], int(round(mult*item[3])), item[4] ] )
                linecount = linecount + 1
                
            obj['arcfeed'] = []
            for item in self.canon.arcfeed:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['arcfeed'].append( [ item[0], [ int(round(mult*item[1][0])), int(round(mult*item[1][1])), int(round(mult*item[1][2])) ], [ int(round(mult*item[2][0])), int(round(mult*item[2][1])), int(round(mult*item[2][2])) ], int(round(mult*item[3])), item[4] ] )
                linecount = linecount + 1

            obj['traverse'] = []
            for item in self.canon.traverse:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['traverse'].append( [ item[0], [ int(round(mult*item[1][0])), int(round(mult*item[1][1])), int(round(mult*item[1][2])) ], [ int(round(mult*item[2][0])), int(round(mult*item[2][1])), int(round(mult*item[2][2])) ], item[3] ] )
                linecount = linecount + 1                

        else:
            obj['feed'] = []    
            for item in self.canon.feed:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['feed'].append( [ item[0], item[1], item[2], item[3] ] )
                linecount = linecount + 1
                
            obj['arcfeed'] = []    
            for item in self.canon.arcfeed:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['arcfeed'].append( [ item[0], item[1], item[2], item[3] ] )
                linecount = linecount + 1
                
            obj['traverse'] = []
            for item in self.canon.traverse:
                if (maxlines > 0 and linecount >= maxlines):
                    break
                obj['traverse'].append( [ item[0], item[1], item[2], item[3] ] )
                linecount = linecount + 1                

        
        string = json.dumps( obj, separators=(',', ':') )
        print "Backplot size: ", len(string)
        return string
            

    def write_x3d( self, filename ):
        f = open(filename,'w')

        f.write('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n')
        f.write('<html xmlns="http://www.w3.org/1999/xhtml">\n')
        f.write('<head>\n')
        f.write('<meta http-equiv="X-UA-Compatible" content="chrome=1" />\n')
        f.write('<meta http-equiv="Content-Type" content="text/html;charset=utf-8" />\n')
        f.write('<title>LinuxCNC Rendered GCode File</title>\n')
        f.write("<script type='text/javascript' src='http://www.x3dom.org/release/x3dom.js'></script>\n")
        f.write("<link rel='stylesheet' type='text/css' href='http://www.x3dom.org/download/x3dom.css'></link>\n")
        f.write('</head>\n')
        
        body = xml.Element('body')
        X3D = xml.Element('X3D')
        X3D.attrib['xmlns']="http://www.web3d.org/specifications/x3d-namespace"
        X3D.attrib['showStat']="false"
        X3D.attrib['showLog']="false"
        X3D.attrib['x']="0px"
        X3D.attrib['y']="0px"
        X3D.attrib['width']="600px"
        X3D.attrib['height']="600px"
        body.append(X3D)
        scene = xml.Element('Scene')
        X3D.append(scene)

        bg = xml.Element('Background')
        bg.attrib['skyColor']='0 0 0'
        scene.append(bg)

        shape = xml.Element('Shape')
        scene.append(shape)
        
        app = xml.Element('Appearance')
        mat = xml.Element('Material')
        mat.attrib['emissiveColor']='0 0 1'
        mat.attrib['diffuseColor']='0 1 0'
        lp = xml.Element('LineProperties')
        lp.attrib['linetype']='1'
        lp.attrib['applied']='true'
        lp.attrib['linewidthScaleFactor']='1'
        app.append(mat)
        app.append(lp)
        shape.append(app)

        vp = xml.Element('Viewpoint')
        vp.attrib['position']="0 0 20"
        scene.append(vp)

        lineset = xml.Element('IndexedLineSet')
        coords = xml.Element('Coordinate')
        lineset.append(coords)
        shape.append(lineset)

        coordstr = ""
        coordidxstr = ""
        idxnum = -1
        lastpnt = None

        for item in self.canon.traverse:
            if (lastpnt == item[1][:3]):
                coordstr = coordstr + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2]) 
                idxnum = idxnum + 1
                coordidxstr = coordidxstr + " " + str(idxnum)
            else:
                if lastpnt is None:
                    coordidxstr = coordidxstr + " " + str(idxnum+1) + " " + str(idxnum+2)
                else:
                    coordidxstr = coordidxstr + " -1 " + str(idxnum+1) + " " + str(idxnum+2)
                coordstr = coordstr + " " + str(item[1][0]) + " " + str(item[1][1]) + " " + str(item[1][2]) + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2]) 
                idxnum = idxnum + 2
            lastpnt = item[2][:3]
            
        for item in self.canon.feed:
            if (lastpnt == item[1][:3]):
                coordstr = coordstr + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2]) 
                idxnum = idxnum + 1
                coordidxstr = coordidxstr + " " + str(idxnum)
            else:
                if lastpnt is None:
                    coordidxstr = coordidxstr + " " + str(idxnum+1) + " " + str(idxnum+2)
                else:
                    coordidxstr = coordidxstr + " -1 " + str(idxnum+1) + " " + str(idxnum+2)
                coordstr = coordstr + " " + str(item[1][0]) + " " + str(item[1][1]) + " " + str(item[1][2]) + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2]) 
                idxnum = idxnum + 2
            lastpnt = item[2][:3]

        for item in self.canon.arcfeed:
            if (lastpnt == item[1][:3]):
                coordstr = coordstr + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2])
                idxnum = idxnum + 1
                coordidxstr = coordidxstr + " " + str(idxnum)
            else:
                if lastpnt is None:
                    coordidxstr = coordidxstr + " " + str(idxnum+1) + " " + str(idxnum+2)
                else:
                    coordidxstr = coordidxstr + " -1 " + str(idxnum+1) + " " + str(idxnum+2)
                coordstr = coordstr + " " + str(item[1][0]) + " " + str(item[1][1]) + " " + str(item[1][2]) + " " + str(item[2][0]) + " " + str(item[2][1]) + " " + str(item[2][2])
                idxnum = idxnum + 2
            lastpnt = item[2][:3]

        coords.attrib['point']=coordstr
        lineset.attrib['coordIndex']=coordidxstr

        xml.ElementTree(body).write(f)

        f.write('</html>')
        f.close()
                

    def get_geometry(self):
        temp = self.inifile.find("DISPLAY", "GEOMETRY")
        if temp:
            self.geometry = temp.upper()
        else:
            self.geometry = 'XYZ'
        return self.geometry

    def report_gcode_error(self, result, seq, filename):
	error_str = gcode.strerror(result)
	sys.stderr.write("G-Code error in " + os.path.basename(filename) + "\n" + "Near line " + str(seq) + " of\n" + filename + "\n" + error_str + "\n")    
	print "G-Code error in " + os.path.basename(filename) + "\n" + "Near line " + str(seq) + " of\n" + filename + "\n" + error_str + "\n"    
            
