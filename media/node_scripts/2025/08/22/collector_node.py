# collector_node.py
import paramiko
import re
import os
import time


class SFTPCollector:
    def __init__(self, params: dict):
        """
        params should be a dict with the following keys:
        - SourceHost, Port, UserName, Password
        - SourceDirectory, MoveSrcToDirectory
        - RegexpFilename (list)
        - DeleteSource (Yes/No)
        - ExternalCommand
        - RemoteSuffix
        - ConnectionRetryCount
        - ConnectionRetryInterval
        """
        self.params = params

    def run(self):
        host = self.params["SourceHost"]
        port = int(self.params["Port"])
        username = self.params["UserName"]
        password = self.params["Password"]
        remote_dir = self.params["SourceDirectory"]
        move_dir = self.params["MoveSrcToDirectory"]
        regex_list = self.params["RegexpFilename"]
        delete_source = self.params["DeleteSource"] == "Yes"

        os.makedirs(move_dir, exist_ok=True)

        # Retry connection
        for attempt in range(1, self.params["ConnectionRetryCount"] + 1):
            try:
                transport = paramiko.Transport((host, port))
                transport.connect(username=username, password=password)
                sftp = paramiko.SFTPClient.from_transport(transport)
                print(f"[INFO] Connected to SFTP {host}:{port} as {username}")
                break
            except Exception as e:
                print(f"[WARN] Connection failed attempt {attempt}: {e}")
                if attempt == self.params["ConnectionRetryCount"]:
                    return {"status": "FAILED", "reason": "Cannot connect to SFTP"}
                time.sleep(self.params["ConnectionRetryInterval"])

        files = sftp.listdir(remote_dir)
        collected_files = []

        for f in files:
            if any(re.match(r, f) for r in regex_list):
                remote_file_path = os.path.join(remote_dir, f)
                local_file_path = os.path.join(move_dir, f)
                sftp.get(remote_file_path, local_file_path)

                # Run external command
                os.system(self.params["ExternalCommand"].replace("&FILE", local_file_path))

                # Rename remote file
                remote_done_path = remote_file_path + self.params["RemoteSuffix"]
                sftp.rename(remote_file_path, remote_done_path)

                # Delete source if required
                if delete_source:
                    sftp.remove(remote_done_path)

                collected_files.append(f)
                print(f"[INFO] Collected {f}")

        sftp.close()
        transport.close()

        return {
            "status": "SUCCESS" if collected_files else "NO FILES",
            "files_collected": collected_files,
            "total_files": len(collected_files)
        }
