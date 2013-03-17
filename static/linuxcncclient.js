// LinuxCNC Web Client
//
// Interface utilities for WebSocket communication with LinuxCNC server
// 
// Function PollLinuxCNC() is entry point, loaded in the body of the web
// page to initiate WebSocket data communications with the server.  
//
// Various pages of information are supported, such as Configure, HAL Setup,
// HAL Graph, Commands, Status, etc..  These pages all use different sections
// of code in this file.
//
//
// Copyright 2012, 2013 Machinery Science, LLC
//
// globals:

// holds the Config File data for the EditableGrid 
var ConfigData = {};
var MenuItems = {};
var ConfigCurrentSectionSelection = "";
var section_list = {};
var modelChangesIgnored = true;
var Metadata;

// HAL Setup
var HALFiles = [];
var HALEditDataChanged = false;
var HALCodeMirror;

// WebSocket for communication to the linuxcnc server
var ws;



function createCookie(name,value,days,path) {
        var expires = ""
	if (days) {
		var date = new Date();
		date.setTime(date.getTime()+(days*24*60*60*1000));
		expires = "; expires="+date.toGMTString();
	}
	document.cookie = name+"="+value+expires+"; path="+path;
}

function readCookie(name) {
	var nameEQ = name + "=";
	var ca = document.cookie.split(';');
	for(var i=0;i < ca.length;i++) {
		var c = ca[i];
		while (c.charAt(0)==' ') c = c.substring(1,c.length);
		if (c.indexOf(nameEQ) == 0) return c.substring(nameEQ.length,c.length);
	}
	return null;
}

function eraseCookie(name) {
	createCookie(name,"",-1);
}


// utility function: convert objects to strings
function dumpObject(obj, maxDepth) {  
    var dump = function(obj, name, depth, tab){  
        if (depth > maxDepth) {  
            return name + ' - Max depth\n';  
        }  

        if (typeof(obj) == 'object') {  
            var child = null;  
            var output = tab + name + '\n';  
            tab += '\t';  
            for(var item in obj){  
                child = obj[item];  
                if (typeof(child) == 'object') {  
                    output += dump(child, item, depth + 1, tab);  
                } else {  
                    output += tab + item + ': ' + child + '\n';  
                }  
            }  
        }  
        return output;  
    }  

    return dump(obj, '', 0, '');  
}  

function confirmExit()
{
    return "You have attempted to leave this page.  If you have made any changes to the fields without clicking the Save button, your changes will be lost.  Are you sure you want to exit this page?";
}
  
// object to store each linuxcnc status variable, and remember
// where to output its value in the table
function StatusObject(name,description, typestr,isarray,arrayIndex)
{
    this.name = name;
    this.description = description;
    this.valtype = typestr;
    this.isarray = isarray;
    this.arrayIndex = arrayIndex;
    this.outputCell = null;
    if (this.isarray)
        this.arrayIndex = arrayIndex;
    else 
        this.arrayIndex = 0;

    this.decorated_name = function() 
    { 
        if (this.isarray) 
            return (this.name + "[" + this.arrayIndex.toString() + "]");
        else 
            return(this.name); 
    };
}

// ******** Arra of StatusObjects
var StatusItems = new Array();

// WebSocket is open: send a request to the server
function StatusSocketOpen()
{
    // Get a list from the server of all linuxcnc status items
    ws.onmessage = StatusListRecieved;
    ws.send( JSON.stringify({ "id":"getlist", "command":"list_get" }) ) ;
}

// the initial request for a list of status items has been
// recieved.  Create the table of values to display.
function StatusListRecieved(evt)
{
    // future socket replies should not go to this function
    // anymore.  Instead, send them on to MessageHandlerServerReply
    ws.onmessage = MessageHandlerServerReply

    // parse the list of status items from the server
    var status_item_list = JSON.parse(evt.data);

    // Create the table of status items
    var root=document.getElementById("LinuxCNCStatusTable");
    var tab=document.createElement('table');
    tab.className="stattable";

    // Create the table header row
    var row=document.createElement('tr');
    cell = document.createElement('th'); 
    cell.appendChild(document.createTextNode( "ID" ));
    row.appendChild(cell);
    cell = document.createElement('th'); 
    cell.appendChild(document.createTextNode( "Name" ));
    row.appendChild(cell);                
    cell = document.createElement('th'); 
    cell.appendChild(document.createTextNode( "Description" ));
    row.appendChild(cell);                
    cell = document.createElement('th'); 
    cell.appendChild(document.createTextNode( "Value" ));
    row.appendChild(cell); 
    tab.appendChild(row);

    // collumn width settings
    var c1 = document.createElement('col');
    c1.setAttribute('width','3%');
    tab.appendChild(c1);
    var c2 = document.createElement('col');
    c2.setAttribute('width','20%');
    tab.appendChild(c2);
    var c3 = document.createElement('col');
    c3.setAttribute('width','30%');
    tab.appendChild(c3);                

    // now create one row for each status item
    var tbo=document.createElement('tbody');

    var abscount = 0;
    for(var i=0;i<status_item_list['data'].length;i++)
    {
        var arcnt ;
		
		if (!status_item_list['data'][i]['watchable'])
			continue;
		
        if (status_item_list['data'][i]["isarray"])
            arcnt = status_item_list['data'][i]["arraylength"];
        else
            arcnt = 1;

        for (var aridx = 0; aridx < arcnt; aridx++)
        {
            abscount++;

            var id = i.toString() + "," + aridx.toString();
            StatusItems[ id ] = new StatusObject( status_item_list['data'][i]["name"], status_item_list['data'][i]["help"], status_item_list['data'][i]["valtype"], status_item_list['data'][i]["isarray"], aridx );
            row=document.createElement('tr');
            var oddeven = "odd";
            if (abscount % 2 == 0)
                oddeven = "stattableodd";
            else
                oddeven = "stattableeven";
            row.className = oddeven;

            cell = document.createElement('td'); 
            if (status_item_list['data'][i]["isarray"])
            {
                cell.appendChild(document.createTextNode(id));
                if (arcnt == 0)
                    cell.setAttribute("name", StatusItems[id].name );
            }
            else
            {
                cell.appendChild(document.createTextNode(i.toString()));
                cell.setAttribute("name", StatusItems[id].name );
            }
            
            row.appendChild(cell);
            

            cell = document.createElement('td'); 
            cell.appendChild(document.createTextNode(StatusItems[id].decorated_name()));
            row.appendChild(cell);

            cell = document.createElement('td'); 
            cell.appendChild(document.createTextNode(StatusItems[id].description))
            row.appendChild(cell);

            cell = document.createElement('td'); 
            row.appendChild(cell);
            outputCell = document.createElement('div');
            cell.appendChild(outputCell);
            StatusItems[id].outputCell = outputCell;

            tbo.appendChild(row);

            ws.send( JSON.stringify({ "id":id, "command":"watch", "name":StatusItems[id].name, "index":aridx }) ) ; 
        }
    }
    tab.appendChild(tbo);

    root.appendChild(tab); 
}

// whenever a new update comes in from the server, 
// update the table or the command response
// with the new value
function MessageHandlerServerReply(evt)
{
    var result = JSON.parse(evt.data);

    var outputcell;
    var prepend = "";

    if ( result["id"] == "COMMAND" || result["id"] == "KEEPALIVE" || result["id"] == "CONFIG" )
        {
        outputcell = document.getElementById("Command_Reply");
        prepend = "Command Response: <br>";
        }
    else
        outputcell = StatusItems[ result["id"] ].outputCell;

    if ( (typeof(result["data"]) == 'object' ) )
        outputcell.innerHTML = prepend + dumpObject(result["data"],4).trim().replace( /\n/g, "<br/>" );
    else
        outputcell.innerHTML = prepend + result["data"].toString().trim().replace( /\n/g, "<br/>" );
}


// **********************************
// Setup COMMAND Processing
// **********************************
function CommandSocketOpen()
{
    // poll for command list
    ws.onmessage = CommandListMessageHndlr;
    ws.send( JSON.stringify({ "id":"getlist", "command":"list_put" }) )
}


// A message from the server telling us the list of commands
function CommandListMessageHndlr(evt)
{
    
    var command_item_list = new Object;

    try {
        var command_item_list = JSON.parse(evt.data);

        command_item_list.data.sort(function(o1,o2){ return o1.name.localeCompare(o2.name); });

        var selectobj = document.getElementById("command_list");

        document.getElementById("Command_Reply").innerHTML = "Ready to send command."
            
        for (var idx=0; idx < (command_item_list.data.length); idx++)
        {
            var newStr = command_item_list.data[idx].name + "(";
            for ( param_idx = 0; param_idx < (command_item_list.data[idx].paramTypes.length); param_idx++ )
            {
                if (param_idx > 0)
                    newStr = newStr + ",";

                if ( command_item_list.data[idx].paramTypes[param_idx].optional )
                    newStr = newStr + "[";

                newStr = newStr + command_item_list.data[idx].paramTypes[param_idx].pname;

                if ( command_item_list.data[idx].paramTypes[param_idx].optional )
                    newStr = newStr + "]";                            
            }
            newStr = newStr + ") : " + command_item_list.data[idx].help;

            selectobj.options[idx] = new Option( newStr, command_item_list.data[idx].name );
        }
    } 
    catch(err) {
        alert( "CommandListMessageHndlr: " + err.message );
    }

    // update the command input box
    CommandSelectChange();

    // new handler for messages
    ws.onmessage = MessageHandlerServerReply
}

// The user has requested the command be sent to the server
function CNCCommandSubmit()
{
    var command_name = document.forms["CommandForm"]["command_name"].value;
    var command_object = new Object();

    var params = document.forms["CommandForm"]["parameters"].value.split(",");

    for (var idx = 0; idx < params.length; idx++)
    {    
        pname = idx.toString();
        command_object[pname] = params[idx].trim();
    }    

    command_object.command = "put";
    command_object.name = command_name;
    command_object.id = "COMMAND";

    var cmd_msg = JSON.stringify( command_object );

    ws.send( cmd_msg ); 
}

// The user has selected a new item from the drop-down list of commands
// Update the command input field
function CommandSelectChange()
{
    document.forms["CommandForm"]["command_name"].value = document.getElementById("command_list").value;
    document.forms["CommandForm"]["parameters"].value = "";
}


// ********************************
// for keepalive tab
// ********************************
function KeepaliveSocketOpen()
{
    ws.onmessage = MessageHandlerServerReply;
    document.getElementById("Command_Reply").innerHTML = "Ready to send keep-alive to server."
}

function CNCKeepaliveSubmit()
{
    ws.send( JSON.stringify({ "id":"KEEPALIVE", "command":"keepalive" }) )   
}

// 
// ********************************
// for halgraph tab
// ********************************
function HALGraphSocketOpen()
{
    ws.onmessage = CNCUpdateHALGraphReply;
    document.getElementById("Command_Reply").innerHTML = "Ready to update HAL Graph."
    CNCUpdateHALGraphSubmit();
}

function CNCUpdateHALGraphImgLoad(evt)
{
}

function CNCUpdateHALGraphReply(evt)
{
    var result = JSON.parse(evt.data);
    
    /*
    var cell = document.createElement('img');
    cell.onload = CNCUpdateHALGraphImgLoad;
    cell.setAttribute('src', result['link'] );
    cell.setAttribute('id', 'HALGraphImg')
    */
   
    var cell = document.createElement('img');
    cell.onload = CNCUpdateHALGraphImgLoad;
    cell.setAttribute('src', result['data'] );
    cell.setAttribute('id', 'HALGraphImg')   
    
    // clear any old images, and insert this new one
    var node = document.getElementById("halgrapharea")
    while (node.hasChildNodes()) {
        node.removeChild(node.lastChild);
    }
    node.appendChild(cell); 
}

function CNCUpdateHALGraphSubmit()
{
    ws.send( JSON.stringify({ "id":"halgraph", "command":"get", "name":"halgraph" }) ) ;
}


// ********************************
// for config tab
// 
// WARNING: Must previously have included editable grid code in html file:
// <script type="text/javascript" src="./editablegrid-2.0.1/editablegrid-2.0.1.js"></script>
// ********************************

var gEditableGrid;


function ConfigSocketOpen()
{
    gEditableGrid = new EditableGrid("LinuxCNC Configuration File", {enableSort: true, editmode:"absolute" } );
    
    Metadata = new Array(  
        {"name":"name","label":"NAME","datatype":"string","editable":true}, 
        {"name":"value","label":"VALUE","datatype":"string","editable":true}, 
        {"name":"comment","label":"COMMENT","datatype":"string","editable":true}, 
        {"name":"help","label":"HELP","datatype":"string","editable":false}, 
        {"name":"action","label":"ACTION","datatype":"string","editable":false} 
     ); 

    
    ws.onmessage = ConfigSocketMessageHandler;
    document.getElementById("Command_Reply").innerHTML = "Ready to load configuration."
    ws.send( JSON.stringify({ "id":"CONFIG", "command":"get", "name":"config" }) ) ;
}

// helper function to get path of a demo image
function image(relativePath) {
    return "/static/editablegrid-2.0.1/images/" + relativePath;
} 

// this function will initialize our editable grid
EditableGrid.prototype.initializeGrid = function(  )
{ 
    var editableGrid = gEditableGrid;
    var grid_str = "gEditableGrid";
    with (this)
    {
        // register the function that will handle model changes
        modelChanged = function(rowIndex, columnIndex, oldValue, newValue, row) {
            if (!modelChangesIgnored)
                window.onbeforeunload = confirmExit;
        };         

        // render for the action column
        setCellRenderer("action", new CellRenderer({
            render: function(cell, value) {
                // this action will remove the row, so first find the ID of the row containing this cell
                var rowId = editableGrid.getRowId(cell.rowIndex);
                cell.innerHTML = "<a onclick=\"if (confirm('Are you sure you want to delete this row? ')) { " + grid_str + ".remove(" + cell.rowIndex + ");  } \" style=\"cursor:pointer\">" +
                "<img src=\"" + image("delete.png") + "\" border=\"0\" alt=\"delete\" title=\"Delete row\"/></a>";
                cell.innerHTML+= "&nbsp;<a onclick=\"" + grid_str + ".duplicate(" + cell.rowIndex + ");\" style=\"cursor:pointer\">" +
                "<img src=\"" + image("duplicate.png") + "\" border=\"0\" alt=\"duplicate\" title=\"Duplicate row\"/></a>";
            }
        }));         
        
    }
}

EditableGrid.prototype.duplicate = function(rowIndex)
{
    // copy values from given row
    var values = this.getRowValues(rowIndex);
    values['name'] = values['name'] + ' (copy)';
    // get id for new row (max id + 1)
    var newRowId = 0;
    for (var r = 0; r < this.getRowCount(); r++) 
        try 
        {
            newRowId = Math.max(newRowId, parseInt(this.getRowId(r)) + 1);
        } catch(err) {}
        
    // add new row
    this.insertAfter(rowIndex, newRowId, values);
    
}; 

function ConfigMenuClick( section_name )
{
    ConfigUpdateFromGridToDataStore( );
    
    modelChangesIgnored = true;
    
    var editableGrid = gEditableGrid;
    var newobj = { "metadata":Metadata, "data":ConfigData[ section_name ]  };
    editableGrid.clearChart();
    editableGrid.load( newobj  );
    editableGrid.initializeGrid();
    editableGrid.renderGrid("ConfigTable", "config_grid");
    editableGrid.sort(0,false,true);

    // move the current_selection to the new menu item.
    for ( k in MenuItems )
        MenuItems[k].setAttribute("id","none");
    MenuItems[section_name].setAttribute("id","current_section");
    
    if (section_list[section_name]['help'].length > 0)
        document.getElementById("grid_title").innerHTML = "<h2>Section: " + section_name + "</h2>" + section_list[section_name]['help'] + "";
    else
        document.getElementById("grid_title").innerHTML = "<h2>Section: " + section_name + "</h2>";
    ConfigCurrentSectionSelection = section_name;
    
    modelChangesIgnored = false;
}

function ConfigUpdateFromGridToDataStore( )
{
    if (ConfigCurrentSectionSelection == "")
        return;
    
    modelChangesIgnored = true;
    
    ConfigData[ConfigCurrentSectionSelection] = [];
    for (var idx = 0; idx < gEditableGrid.getRowCount(); idx++ )
    {
        var vals = gEditableGrid.getRowValues(idx);
        vals['section'] = ConfigCurrentSectionSelection;
        var newobj = { 'id':gEditableGrid.getRowId(idx), 'values':vals };
        ConfigData[ConfigCurrentSectionSelection].push(newobj);
    }
    
    modelChangesIgnored = false;
}

function ConfigRemoveSection()
{
    var val = window.confirm("Remove Current Section (" + ConfigCurrentSectionSelection + "):\n\n Do you want to continue?");
    if (val == null || val == false)
        return;    
    
    delete ConfigData[ConfigCurrentSectionSelection];
    delete section_list[ConfigCurrentSectionSelection];
    
    ConfigCurrentSectionSelection = "";
    
    // the model has changed
    window.onbeforeunload = confirmExit;
    
    // update the menus with the new section
    ConfigUpdateSectionMenus();
}

function ConfigAddSection()
{
    var val = window.prompt("Create new section in INI file:\n\nWhat is the name of the new section?", "");
    if (val == null || val == "")
        return;
    
    val = val.toUpperCase();
    
    if (val in ConfigData)
    {
        alert("Section name already exists.");
        return;
    }
    
    // create the entry for data for the new section
    ConfigData[val] = [];
    section_list[val] = { 'comment':'', 'help':'' };
    
    // update the menus with the new section
    ConfigUpdateSectionMenus();
    
    // now select the new section
    ConfigMenuClick(val);
    
    // start with a new blank value
    ConfigAddValue();
    
    // the model has changed
    window.onbeforeunload = confirmExit;    
    
}

function ConfigAddValue()
{
    idv = 1;
    for (k in ConfigData)
        for (v in ConfigData[k])
            try {
                if (+ConfigData[k][v]["id"] >= idv)
                    idv = +ConfigData[k][v]["id"] + 1;
            } 
            catch(err) {}
     
    gEditableGrid.insertAfter( 0, idv, { 'name':'', 'value':'', 'comment':'', 'help':'', 'action':'' } );
    ConfigData[ConfigCurrentSectionSelection].push( { 'id':idv, 'values':{ 'section':ConfigCurrentSectionSelection, 'name':"", 'value':"", 'comment':'', 'help':'' } } )
    
    // the model has changed
    window.onbeforeunload = confirmExit;
}

function ConfigWrite()
{
    // confirm
    if (!window.confirm("Warning: This will overwrite the ini file on the LinuxCNC system.  Continue?"))
        return;
    
    // synchronize data from data store and grid
    ConfigUpdateFromGridToDataStore( );
    
    var newobj = []
    for (k in ConfigData)
        for (v in ConfigData[k])
        {
            newobj.push(ConfigData[k][v]);
            // eliminate uneeded elements
            newobj[ newobj.length-1 ]['help'] = "";
            newobj[ newobj.length-1 ]['action'] = "";
        }
            
    var cmd = JSON.stringify({ "id":"Write_Config", "command":"put", "name":"config", "data":{ "parameters":newobj, "sections":section_list } });
    ws.send( cmd ) ;
    
    window.onbeforeunload = null;
}

function ConfigUpdateSectionMenus()
{
    // create menu items for each section
    var menu_div = document.getElementById("left_menu");
    
    // create a list of sections
    var section_sorted_list = [];
    for (var k in ConfigData ) section_sorted_list.push(k);
    section_sorted_list.sort();    
    
    // remove all menu items
    while (menu_div.hasChildNodes()) {
        menu_div.removeChild(menu_div.lastChild);
    }
    
    // now create the new menu
    var menu_list = document.createElement("ul");    
    for ( var idx = 0; idx < section_sorted_list.length; idx++ )
    {
        var section_name = section_sorted_list[idx];
        
        var menu_item = document.createElement("li");
        var menu_link = document.createElement("a");
        var menu_text = document.createElement("div");
        
        menu_link.onclick = (function(n){ return function(){ ConfigMenuClick(n); return false;} }(section_name));
        menu_link.setAttribute("href", "#" );
        menu_text.innerHTML = section_name;
        
        MenuItems[section_name] = menu_link;
        
        menu_item.appendChild(menu_link);
        menu_link.appendChild(menu_text);
        menu_list.appendChild(menu_item);
    }
    menu_div.appendChild(menu_list);
    
    // default selection to the first menu item 
    ConfigMenuClick(section_sorted_list[0]);
}

function ConfigSocketMessageHandler(evt)
{
    var result = JSON.parse(evt.data);

    if (result["id"] == "CONFIG")
    {

        // Parse out all the unique section headers
        section_list = result["data"]["sections"];
        
        // sort the list
        var section_sorted_list = [];
        for (var k in section_list ) section_sorted_list.push(k);
        section_sorted_list.sort();
        

        // load the data into sections
        ConfigData = {};
        for (var idx = 0; idx < section_sorted_list.length; idx++ )
        {
            ConfigData[ section_sorted_list[idx] ] = [];
        }
        for ( var idx in result["data"]["parameters"] )
        {
            var section = result["data"]["parameters"][idx]["values"]["section"];
            ConfigData[ section ].push( result["data"]["parameters"][idx] );
        }    

        for (sec in ConfigData )
            for ( item in ConfigData[sec] )
                ConfigData[sec][item]['values']['action'] = '';

        // put section menus on the left side
        ConfigUpdateSectionMenus();
    } else {
        alert("Last command reply: " + result["code"].substring(1));
    }

    // update the status 
    var outputcell = document.getElementById("Command_Reply");
    outputcell.innerHTML = "Last Command Reply: " + result["code"];
}


// ********************************
// for HAL Setup tab
// 
// WARNING: Must previously have included editable grid code in html file:
// <script type="text/javascript" src="./editablegrid-2.0.1/editablegrid-2.0.1.js"></script>
// ********************************


 
function isFullScreen(cm) {
    return /\bCodeMirror-fullscreen\b/.test(cm.getWrapperElement().className);
}
function winHeight() {
    return window.innerHeight || (document.documentElement || document.body).clientHeight;
}
function setFullScreen(cm, full) {
    
    if (full) {
        var container = document.getElementById("HALTextEditDIV");
        container.parentNode.removeChild(container);
        document.getElementById("wrapbig").appendChild(container);
        
        var wrap = cm.getWrapperElement(), scroll = cm.getScrollerElement();
        wrap.className += " CodeMirror-fullscreen";
        scroll.style.height = winHeight() + "px";
        document.documentElement.style.overflow = "hidden";
    } else {
        var container = document.getElementById("HALTextEditDIV");
        container.parentNode.removeChild(container);
        document.getElementById("wrap").appendChild(container);        
        
        var wrap = cm.getWrapperElement(), scroll = cm.getScrollerElement();
        wrap.className = wrap.className.replace(" CodeMirror-fullscreen", "");
        scroll.style.height = "";
        document.documentElement.style.overflow = "";
    }
    cm.refresh();
}

function HALSetupSocketOpen()
{
    HALEditDataChanged = false;
    HALSetupCurrentFile = '';
    window.onbeforeunload = null;
    
    ws.onmessage = HALSetupSocketMessageHandler;
    document.getElementById("Command_Reply").innerHTML = "Ready to load configuration."
    ws.send( JSON.stringify({ "id":"HALSetup1", "command":"get", "name":"config_item", "section":"HAL", "parameter":"HALFILE" }) ) ;
    
    try {
        CodeMirror.connect(window, "resize", function() {
            var showing = document.body.getElementsByClassName("CodeMirror-fullscreen")[0];
            if (!showing) return;
            showing.CodeMirror.getScrollerElement().style.height = winHeight() + "px";
        });        
        
        HALCodeMirror = CodeMirror.fromTextArea( document.getElementById("HALConfigText"),  { 
            lineNumbers: true,
            fixedGutter: true,
            matchBrackets: true,
            autoSelect: true,
            theme: 'default',
            onChange: HALSetupTextEdited,
            extraKeys: {
                "F11": function(HALCodeMirror) {
                    setFullScreen(HALCodeMirror, !isFullScreen(HALCodeMirror));
                },
                "Esc": function(HALCodeMirror) {
                    if (isFullScreen(HALCodeMirror)) setFullScreen(HALCodeMirror, false);
                }        
            }
        });
    }
    catch(err) {
        alert( "HALSetupSocketOpen: " + err.message );
    }

}

function HALSetupSave()
{
    if (HALSetupCurrentFile == "")
        return;
    
    if (!HALEditDataChanged)
    {
        alert("File not changed.");
        return
    }

    ws.send( JSON.stringify({ "id":"HALWriteData", "command":"put", "name":"halfile", "filename":HALSetupCurrentFile, 'data':HALCodeMirror.getValue() }) ) ;

    HALEditDataChanged = false;
}

function HALSetupSocketMessageHandler(evt)
{
    var result = JSON.parse(evt.data);
    
    if ( result['id'] == 'HALSetup1' )
    {
        HALFiles = [];
        for ( var num in result['data']['parameters'] )
        {
            HALFiles.push( result['data']['parameters'][num]['values']['value'] );
        }
        ws.send( JSON.stringify({ "id":"HALSetup2", "command":"get", "name":"config_item", "section":"HAL", "name":"POSTGUI_HALFILE" }) ) ;
    } else if ( result['id'] == 'HALSetup2' )
    {    
        for ( var num in result['data']['parameters'] )
        {
            HALFiles.push( result['data']['parameters'][num]['values']['value'] );
        }
        
        ws.send( JSON.stringify({ "id":"HALSetup3", "command":"get", "name":"config_item", "section":"HAL", "name":"SHUTDOWN" }) ) ;

    } else if ( result['id'] == 'HALSetup3' )
    {    
        for ( var num in result['data']['parameters'] )
        {
            HALFiles.push( result['data']['parameters'][num]['values']['value'] );
        }
        HALFiles.sort();
        HALSetupUpdateSectionMenus();
    } else if ( result['id'] == 'HALSetupData' )
    {
        HALCodeMirror.setValue( (result['data']) );
        HALSetupTextNotEdited();
    } else if ( result['id'] == "HALWriteData")
        alert("Data sent.  Server replied: " + result["code"].substring(1));
    
    // update the status 
    var outputcell = document.getElementById("Command_Reply");
    outputcell.innerHTML = "Last Command Reply: " + result["code"];
    
}

function HALSetupTextEdited(editor, change_location)
{
    HALEditDataChanged = true;
    window.onbeforeunload = confirmExit;
}


function HALSetupTextNotEdited()
{
    HALEditDataChanged = false;
    window.onbeforeunload = null;
}

function HALSetupMenuClick( file_name )
{
    if (HALEditDataChanged)
        if (false == confirm('Are you sure you want to discard your changes? '))
            return;
    
    ws.send( JSON.stringify({ "id":"HALSetupData", "command":"get", "name":"halfile", "filename":file_name }) ) ;
    HALSetupCurrentFile = file_name;
    
    // move the current_selection to the new menu item.
    for ( k in MenuItems )
        MenuItems[k].setAttribute("id","none");
    MenuItems[file_name].setAttribute("id","current_section");

    document.getElementById("grid_title").innerHTML = "<h2>HAL File " + file_name + "</h2>";
    
    HALSetupTextNotEdited();
}

function HALSetupUpdateSectionMenus()
{
    // create menu items for each section
    var menu_div = document.getElementById("left_menu");
    
    // remove all menu items
    while (menu_div.hasChildNodes()) {
        menu_div.removeChild(menu_div.lastChild);
    }
    
    // now create the new menu
    var menu_list = document.createElement("ul");    
    for ( var idx = 0; idx < HALFiles.length; idx++ )
    {
        var file_name = HALFiles[idx];
        
        var menu_item = document.createElement("li");
        var menu_link = document.createElement("a");
        var menu_text = document.createElement("div");
        
        menu_link.onclick = (function(n){ return function(){ HALSetupMenuClick(n); return false;} }(file_name));
        menu_link.setAttribute("href", "#" );
        menu_text.innerHTML = file_name;
        
        MenuItems[file_name] = menu_link;
        
        menu_item.appendChild(menu_link);
        menu_link.appendChild(menu_text);
        menu_list.appendChild(menu_item);
    }
    menu_div.appendChild(menu_list);
    
    // default selection to the first menu item 
    if ( HALFiles.length > 0)
        HALSetupMenuClick(HALFiles[0]);
}

// ********************************
// for Sandbox tab
// 
// Simple interface for manually sending websocket commands -- for debug use
// ********************************

function SandboxSocketOpen()
{
    ws.onmessage = SandboxSocketMessageHandler;
}

function SandboxSend()
{
    
    ws.send( document.getElementById("sandbox_input").value );
}

function SandboxSocketMessageHandler(evt)
{
    document.getElementById("sandbox_output").value = evt.data;
}


// ********************************
// for System tab
// 
// Simple interface for starting and stopping LinuxCNC
// ********************************
function SystemSocketOpen()
{
    ws.onmessage = SystemSocketMessageHandler;
    document.getElementById("Command_Reply").innerHTML = "Server Connection Initiated"

   // Get a list from the server of all linuxcnc status items
    ws.send( JSON.stringify({ "id":"STATUS_CHECK", "command":"watch", "name":"estop" }) ) ;
    ws.send( JSON.stringify({ "id":"INI_MONITOR", "command":"watch", "name":"ini_file_name" }) ) ;
}

function SystemSocketMessageHandler(evt)
{
    var result = JSON.parse(evt.data);
    
    if ( result["id"] == "STATUS_CHECK" )
    {
        if (result["data"][0] != "?")
            document.getElementById("LinuxCNCRunStatus").innerHTML = "Running";
        else
            document.getElementById("LinuxCNCRunStatus").innerHTML = "Down";
    } else if ( result["id"] == "INI_MONITOR" )
    {
        document.forms["INIForm"]["ini_name"].value = result["data"];
    } else if ( result["id"] == "INI_SET" )
    {
        document.getElementById("Command_Reply").innerHTML = "Server replied: " + result["code"];
    }

}

function SystemShutdown()
{
    ws.send( JSON.stringify({ "id":"SystemShutdown", "command":"shutdown" }) ) ;
}

function SystemStart()
{
    ws.send( JSON.stringify({ "id":"SystemStart", "command":"startup" }) ) ;
}

function SystemSetINI()
{
    ws.send( JSON.stringify({ "id":"INI_SET", "command":"put", "name":"ini_file_name", "ini_file_name":document.forms["INIForm"]["ini_name"].value }));
}

// Function to monitor the websocket status.  
// Don't let the user think we have a connection when we dont -- show alternate
// text when there is no socket
function MonitorWebSocket()
{
    if (ws.readyState != 1)
    {
        document.getElementById("WebSocketData").style.display="none";
        document.getElementById("AltWebSocketData").style.display="block";
    }
    else
    {
        document.getElementById("WebSocketData").style.display="block";
        document.getElementById("AltWebSocketData").style.display="none";
    }
}


function LoginMessage(evt)
{
    var result = JSON.parse(evt.data);
    if (result["code"] == "?OK")
    {
        ws.custom_onopen();
        document.getElementById("logout_button").innerHTML = "Logout User " + readCookie("linuxcnc_username");
    }
    else
    {
        Logout();
        alert("Error Logging in.  Server message: " + result["code"]);
    }
}

function Logout()
{
    eraseCookie("linuxcnc_username");
    eraseCookie("linuxcnc_password");
    ws.close();        
    document.getElementById("logout_button").style.display = "none";
}

function Login()
{
    var name = readCookie("linuxcnc_username");
    var pw = readCookie("linuxcnc_password");
    if (name == null || pw == null)
        {
            name = prompt("Enter Username");
            if (name != null)
            {
                pw = prompt("Enter password");
                if (pw != null)
                {
                    createCookie("linuxcnc_username",name,365,"/"); 
                    createCookie("linuxcnc_password",pw,365,"/");
                }
            }
        }
    
    if (name != null && pw != null)
    {
        ws.send( JSON.stringify({ "id":"Login", "user":name, "password":pw }) ) ;
        ws.onmessage = LoginMessage;
    } else {
        Logout();
    }
}

// INITIAL CALL on onload event.  This starts off the sequence
// of getting a list of status items from the server, making a 
// table to display them, and then monitoring updates to their 
// values.
function PollLinuxCNC( type )
{
    document.getElementById("AltWebSocketData").style.display="none"; 
    
    ws = new WebSocket("ws://" + document.domain + ":8000/websocket/linuxcnc","linuxcnc");
    
    ws.onopen = Login;
    
    if (type == 'status')
        ws.custom_onopen = StatusSocketOpen;
    else if (type == 'commands')
        ws.custom_onopen = CommandSocketOpen;
    else if (type == 'keepalive')
        ws.custom_onopen = KeepaliveSocketOpen;
    else if (type == 'halgraph')
        ws.custom_onopen = HALGraphSocketOpen;
    else if (type == 'config')
        ws.custom_onopen = ConfigSocketOpen;
    else if (type == 'halsetup')
        ws.custom_onopen = HALSetupSocketOpen;
    else if (type == 'sandbox')
        ws.custom_onopen = SandboxSocketOpen;
    else if (type == 'system')
        ws.custom_onopen = SystemSocketOpen;

    if (!( ws.custom_onopen == undefined ))
    {
        // monitor for a closed WebSocket
        window.setInterval(MonitorWebSocket,250);
    } else {
        document.getElementById("WebSocketData").style.display="block";
        document.getElementById("AltWebSocketData").style.display="none";        
    }
}
