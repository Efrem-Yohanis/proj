class SFTPCollector:
    def __init__(self, host=None, username=None, password=None, port=None, path=None, regix=None):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.path = path
        self.regix = regix

    def run(self):
        # Use the parameters passed dynamically
        print(f"Connecting to {self.host} as {self.username}")
        print(f"Collecting files from {self.path} with pattern {self.regix} on port {self.port}")
        # Implement real SFTP logic here...
        print("Files collected successfully")
