#!/usr/bin/env python
"""
A2A-MCP-Server Configuration Creator for Claude Desktop

This script helps users create the correct configuration entry for their
claude_desktop_config.json file to use the A2A-MCP-Server with Claude Desktop.
"""

import json
import os
import platform
import sys
from pathlib import Path

def get_config_path():
    """Return the default path to the Claude Desktop config file based on the operating system."""
    if platform.system() == "Windows":
        return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
    elif platform.system() == "Darwin":  # macOS
        return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    else:  # Linux and others
        return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")

def get_python_path():
    """Return the path to the current Python executable."""
    return sys.executable

def find_script_path():
    """Try to find the default a2a_mcp_server.py path."""
    # Check current directory
    current_dir = os.getcwd()
    script_path = os.path.join(current_dir, "a2a_mcp_server.py")
    if os.path.exists(script_path):
        return script_path
    
    # Check parent directory
    parent_dir = os.path.dirname(current_dir)
    script_path = os.path.join(parent_dir, "a2a_mcp_server.py")
    if os.path.exists(script_path):
        return script_path
    
    # Common structure: Check A2A-MCP-Server directory
    script_path = os.path.join(current_dir, "A2A-MCP-Server", "a2a_mcp_server.py")
    if os.path.exists(script_path):
        return script_path
    
    # Check Desktop location
    home_dir = os.path.expanduser("~")
    desktop_path = os.path.join(home_dir, "Desktop", "A2A-MCP-Server", "a2a_mcp_server.py")
    if os.path.exists(desktop_path):
        return desktop_path
    
    # If no default found, return empty string
    return ""

def find_repo_path():
    """Try to find the default A2A-MCP-Server repository path."""
    # Check if current directory is the repository
    current_dir = os.getcwd()
    if os.path.exists(os.path.join(current_dir, "a2a_mcp_server.py")):
        return current_dir
    
    # Check parent directory
    parent_dir = os.path.dirname(current_dir)
    if os.path.exists(os.path.join(parent_dir, "a2a_mcp_server.py")):
        return parent_dir
    
    # Check A2A-MCP-Server directory
    repo_path = os.path.join(current_dir, "A2A-MCP-Server")
    if os.path.exists(repo_path):
        return repo_path
    
    # Check Desktop location
    home_dir = os.path.expanduser("~")
    desktop_path = os.path.join(home_dir, "Desktop", "A2A-MCP-Server")
    if os.path.exists(desktop_path):
        return desktop_path
    
    # If no default found, return empty string
    return ""

def create_pypi_config():
    """Create configuration for PyPI installation."""
    # For Claude Desktop, we must use stdio transport
    env = {
        "MCP_TRANSPORT": "stdio"
    }
    
    config = {
        "command": "python",
        "args": [
            "-m",
            "a2a_mcp_server"
        ],
        "env": env
    }
    
    # Ask if user wants to add any additional environment variables
    if input("Do you want to add additional environment variables? (y/n): ").lower() == 'y':
        print("Enter environment variables (empty name to finish):")
        while True:
            name = input("Environment variable name: ").strip()
            if not name:
                break
            value = input(f"Value for {name}: ").strip()
            env[name] = value
    
    return config

def create_local_config():
    """Create configuration for local installation."""
    # Get Python executable
    python_path = input(f"Python executable path (default: {get_python_path()}): ").strip()
    if not python_path:
        python_path = get_python_path()
    
    # Get script path with default
    default_script_path = find_script_path()
    script_prompt = f"Path to a2a_mcp_server.py"
    if default_script_path:
        script_prompt += f" (default: {default_script_path})"
    script_prompt += ": "
    
    script_path = input(script_prompt).strip()
    if not script_path and default_script_path:
        script_path = default_script_path
        
    while not script_path or not os.path.exists(script_path):
        print("Error: File does not exist.")
        script_path = input(script_prompt).strip()
        if not script_path and default_script_path:
            script_path = default_script_path
            if os.path.exists(script_path):
                break
    
    # Get repo path for PYTHONPATH with default
    default_repo_path = find_repo_path()
    repo_prompt = f"Path to A2A-MCP-Server repository"
    if default_repo_path:
        repo_prompt += f" (default: {default_repo_path})"
    repo_prompt += ": "
    
    repo_path = input(repo_prompt).strip()
    if not repo_path and default_repo_path:
        repo_path = default_repo_path
        
    while not repo_path or not os.path.exists(repo_path):
        print("Error: Directory does not exist.")
        repo_path = input(repo_prompt).strip()
        if not repo_path and default_repo_path:
            repo_path = default_repo_path
            if os.path.exists(repo_path):
                break
    
    # For Claude Desktop, we must use stdio transport
    env = {
        "MCP_TRANSPORT": "stdio",
        "PYTHONPATH": repo_path
    }
    
    config = {
        "command": python_path,
        "args": [
            script_path
        ],
        "env": env
    }
    
    # Ask if user wants to add other environment variables
    if input("Do you want to add additional environment variables? (y/n): ").lower() == 'y':
        print("Enter environment variables (empty name to finish):")
        while True:
            name = input("Environment variable name: ").strip()
            if not name:
                break
            value = input(f"Value for {name}: ").strip()
            config["env"][name] = value
    
    return config

def update_claude_config(config, key_name):
    """Update the Claude Desktop config file with the new A2A MCP Server configuration."""
    config_path = get_config_path()
    
    try:
        # Check if the config file exists
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                try:
                    full_config = json.load(f)
                except json.JSONDecodeError:
                    print(f"Error: The config file at {config_path} contains invalid JSON.")
                    return False
            
            # Initialize mcpServers if it doesn't exist
            if 'mcpServers' not in full_config:
                full_config['mcpServers'] = {}
            
            # Add or update the A2A MCP Server configuration
            full_config['mcpServers'][key_name] = config
            
            # Write the updated config back to the file
            with open(config_path, 'w') as f:
                json.dump(full_config, f, indent=2)
            
            print(f"Successfully updated Claude Desktop config at {config_path}")
            return True
        else:
            print(f"Config file not found at {config_path}. Creating a new one.")
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            full_config = {
                'mcpServers': {
                    key_name: config
                }
            }
            
            with open(config_path, 'w') as f:
                json.dump(full_config, f, indent=2)
            
            print(f"Created new Claude Desktop config at {config_path}")
            return True
    
    except Exception as e:
        print(f"Error updating config file: {e}")
        return False

def main():
    """Main function to run the configuration creator."""
    print("=" * 80)
    print("A2A-MCP-Server Configuration Creator for Claude Desktop")
    print("=" * 80)
    print("\nThis script will help you create a configuration entry for Claude Desktop.")
    print("The configuration will be saved to your claude_desktop_config.json file.")
    print("\nImportant: For Claude Desktop, the MCP_TRANSPORT will be set to 'stdio'")
    print("as this is required for Claude Desktop to communicate with MCP servers.")
    print("\nChoose your installation method:")
    
    choice = None
    while choice not in ('1', '2'):
        print("1. PyPI Installation (installed with pip)")
        print("2. Local Installation (cloned from repository)")
        choice = input("Enter your choice (1/2): ")
    
    key_name = input("\nName for this MCP server in Claude Desktop (default: a2a): ").strip()
    if not key_name:
        key_name = "a2a"
    
    if choice == '1':
        config = create_pypi_config()
    else:
        config = create_local_config()
    
    # Display the generated configuration
    print("\nGenerated Configuration:")
    print(json.dumps({key_name: config}, indent=2))
    
    # Check config for stdio transport
    if config.get("env", {}).get("MCP_TRANSPORT") != "stdio":
        print("\nWARNING: MCP_TRANSPORT is not set to 'stdio'. Claude Desktop requires stdio transport.")
        if input("Set MCP_TRANSPORT to 'stdio'? (y/n): ").lower() == 'y':
            if "env" not in config:
                config["env"] = {}
            config["env"]["MCP_TRANSPORT"] = "stdio"
            print("MCP_TRANSPORT set to 'stdio'.")
            print("\nUpdated Configuration:")
            print(json.dumps({key_name: config}, indent=2))
    
    # Ask if the user wants to update their Claude Desktop config
    if input("\nDo you want to update your Claude Desktop config file? (y/n): ").lower() == 'y':
        update_claude_config(config, key_name)
    else:
        print("\nConfiguration not saved. You can manually add it to your claude_desktop_config.json file.")
    
    print("\nDone!")

if __name__ == "__main__":
    main()
