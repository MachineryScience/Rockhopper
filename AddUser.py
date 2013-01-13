
import sys
import hashlib
from ConfigParser import SafeConfigParser

def main():
    if len(sys.argv) < 3:
        sys.exit('Usage: AddUser.py <username> <password>')
        
    parser = SafeConfigParser()
    parser.read('users.ini')
    
    if not parser.has_section('users'):
        parser.add_section('users')
    
    parser.set('users', sys.argv[1], hashlib.md5(sys.argv[2].strip()).hexdigest() )
    
    file = open('users.ini', 'w')
    file.write('#This is the list of users/encrypted passwords for the Linux CNC Web Server\n\n')
    file.write('#Use the included python script AddUser to add users\n')
    file.write('#Users can be removed by deleting the entries in this file with their user name\n')
    file.write('#This file will be auto-generated and overwritten by the AddUser program\n\n')
    parser.write(file)
    file.close()
    

# auto start if executed from the command line
if __name__ == "__main__":
    main()
