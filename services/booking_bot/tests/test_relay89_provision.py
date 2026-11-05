"""Regression test for interrupted SSH host-key file provisioning."""
import io
import stat
from types import SimpleNamespace

from scripts.provision_bot_relay89 import ensure_file


class FakeSFTP:
    def __init__(self):
        self.files = {"/safe/known_hosts.tmp": b""}

    def open(self, path, mode):
        if "r" in mode:
            if path not in self.files:
                raise FileNotFoundError(path)
            return io.BytesIO(self.files[path])
        if "x" in mode and path in self.files:
            raise FileExistsError(path)
        if "w" not in mode:
            raise OSError("File not open for writing")
        stream = io.BytesIO()
        close = stream.close

        def save():
            self.files[path] = stream.getvalue()
            close()

        stream.close = save
        return stream

    def lstat(self, path):
        return SimpleNamespace(st_size=len(self.files[path]), st_mode=stat.S_IFREG)

    def remove(self, path):
        del self.files[path]

    def chmod(self, path, mode):
        pass

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def close(self):
        pass


class FakeClient:
    def __init__(self):
        self.sftp = FakeSFTP()

    def open_sftp(self):
        return self.sftp


def test_interrupted_empty_temp_is_replaced_and_host_key_written():
    client = FakeClient()
    ensure_file(client, "/safe/known_hosts", "trusted-key\n", 0o644)
    assert client.sftp.files == {"/safe/known_hosts": b"trusted-key\n"}
