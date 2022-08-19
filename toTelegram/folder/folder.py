class Folder:
    def __init__(self,name, path, dev, inodo, modified) -> None:
        self.name=name
        self.path=path
        self.dev=dev
        self.inodo=inodo
        self.modified=modified
    def get_files(self):
        files=[]
        with os.scandir(self.path) as it:
            for entry in it:
                if entry.is_file():
                    stat_result=os.stat(entry)
                    name=entry.name
                    sise=stat_result.st_size
                    path=entry.path
                    dev= stat_result.st_dev
                    inodo= stat_result.st_ino
                    modified= stat_result.st_mtime
                    file=File(name, path, sise, dev, inodo, modified)
                    files.append(file)
        return files