"""A shared in-memory FileStation for the offline trash/delete tests.

Both `test_trash.py` (placement rules) and `test_delete_flow.py` (the real
`_handle_delete` handler) need the same three FileStation operations with the
same failure knobs; one copy keeps the two suites from drifting.
"""


class FakeFileStation:
    """Just enough FileStation: existence, mkdir, move, delete.

    Failure knobs for exercising every branch:

    - ``fail_create``: `create_directory` raises (share cannot host a trash)
    - ``fail_move``: `move_file` raises
    - ``drop_source_on_failure``: the source vanishes when the move fails,
      the "outcome unknown, must not hard-delete" case
    - ``pretend_move``: the move reports success without moving anything
    - ``deleted``: every `delete()` call is recorded here
    """

    def __init__(self, dirs=(), files=()):
        self.dirs = set(dirs)
        self.files = set(files)
        self.deleted = []
        self.fail_create = False
        self.fail_move = False
        self.drop_source_on_failure = False
        self.pretend_move = False

    def _exists(self, path):
        return path in self.dirs or path in self.files

    def get_file_info(self, path):
        if path in self.dirs:
            return {"type": "directory", "path": path}
        if path in self.files:
            return {"type": "file", "path": path}
        raise FileNotFoundError(path)

    def create_directory(self, parent, name, force_parent=False):
        if self.fail_create:
            raise RuntimeError("permission denied")
        path = f"{parent.rstrip('/')}/{name}"
        if path in self.dirs:
            raise RuntimeError("already exists")
        if force_parent:
            parts = path.strip("/").split("/")
            for index in range(1, len(parts)):
                self.dirs.add("/" + "/".join(parts[:index]))
        self.dirs.add(path)
        return {"success": True, "full_path": path}

    def move_file(self, source, destination, overwrite=False):
        if self.fail_move:
            if self.drop_source_on_failure:
                self.files.discard(source)
                self.dirs.discard(source)
            raise RuntimeError("move failed")
        if self.pretend_move:
            return {"success": True}
        if self._exists(destination):
            raise RuntimeError("destination exists")
        if source in self.files:
            self.files.remove(source)
            self.files.add(destination)
        elif source in self.dirs:
            self.dirs.remove(source)
            self.dirs.add(destination)
        else:
            raise FileNotFoundError(source)
        return {"success": True, "destination": destination}

    def delete(self, path):
        self.deleted.append(path)
        self.files.discard(path)
        self.dirs.discard(path)
        return {"success": True, "path": path}
