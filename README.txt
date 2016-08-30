The LinuxCNC WebSocket Server is a lightweight web server customized to provide information and control of a running LinuxCNC machine control system.

The server is based on the Tornado open source web server framework.  Tornado is written in Python, and the LinuxCNC Web Socket Server is also written using the Python programming language.

The interface to the LinuxCNC system uses the Python API for LinuxCNC.  Documentation for this API is at http://www.linuxcnc.org/docs/html/common/python-interface.html

The server also uses the halcmd program, a part of LinuxCNC which can access and configure the HAL layer of LinuxCNC.

To communicate with the LinuxCNC WebSocket Server, a client application opens a WebSocket with the server, and sends text commands.  All commands result in an immediate reply, such as the result of executing the command or some status information. Some commands will also send further replies, such as notifying the client when a watched status variable has changed.

Using this WebSocket based interface, a remote program can query status, send commands, and otherwise control the running LinuxCNC machine controller.

All commands and replies are formatted using JSON (JavaScript Object Notation).  This is a text based, human readable format which is widely supported in many programming platforms.


This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
