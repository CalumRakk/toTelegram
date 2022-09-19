from typing import List
import os

import pymongo  # pip install pymongo
from pymongo.errors import BulkWriteError



MONGODB_CONNECTION_STRING = os.environ["MONGODB_CONNECTION_STRING"].split(";")[0]


class Database:
    def __init__(self):
        self.myclient = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
        self.database = self.myclient["toTelegram"]
        self.profiles = self.database["folders"]

    def insert_video(self, video) -> None:
        """
        Actualiza el video en la base de datos.
        """
        self.videos.replace_one({"_id": video.id}, video.to_dict())

    def find_id_from_videodocuments(self, Id) -> List[str]:

        documents = []
        match = self.videos.find({"profileId": Id}, {"_id": 1})
        if match:
            documents.extend(match)

        match = self.videos.find({"authorId": Id}, {"_id": 1})
        if match:
            documents.extend(match)
        return [doc["_id"] for doc in documents]

    def find_videodocuments(self, profile) -> List[dict]:
        """
        Obtiene todos los videodocument del perfil.
        """
        documents = []

        match = self.videos.find({"profileId": profile.id})
        if match:
            documents.extend(match)

        match = self.videos.find({"authorId": profile.id})
        if match:
            documents.extend(match)
        return documents

    def insert_videos(self, items) -> None:
        """
        Inserta una lista de videos en la base de datos.
        """
        try:
            if len(items) > 0:
                documents = [item.to_dict() for item in items]
                self.videos.insert_many(documents)
        except BulkWriteError:
            Id = documents[0]["authorId"]
            ids = self.find_id_from_videodocuments(Id)
            new_documents = []
            for document in documents:
                if document["_id"] not in ids:
                    new_documents.append(document)
            if len(new_documents) > 0:
                self.videos.insert_many(new_documents)

    def find_profiledocuments(self) -> List[dict]:
        """
        Obtiene todos los profiledocument.
        """
        # return self.profiles.find(
        #     {"$or": [{"online": True}, {"online": {"$exists": False}}]}
        # ).sort("last_scraping_time",-1)
        return self.profiles.find({"online": True}).sort("last_scraping_time",1)

    def find_profile(self, target: str) -> dict:
        """
        Devuelve un profiledocument
        target: Debe ser un String que contenga el id (solo digitos) o el nombre del usuario
        """
        # Elige si buscar por id o por nombre
        if target.isdigit():
            full_info = self.profiles.find_one({"_id": target})
        else:
            full_info = self.profiles.find_one({"uniqueId": target})
            if full_info == None:
                full_info = self.profiles.find_one(
                    {"user.uniqueId": target, "online": True}
                )

        if full_info == None:  # Si no existe el perfil, devuelve None
            return None

        return full_info

    def insert_profile(self, document: dict) -> None:
        # Obtengo el documento del perfil
        self.profiles.insert_one(document)
        print("Inserted profile")

    def update_video(self, video) -> None:
        """
        Actualiza el video en la base de datos.
        """
        self.videos.replace_one({"_id": video.id}, video.to_dict())

    def update_profiledocument(self, profiledocument: dict):
        self.profiles.replace_one({"_id": profiledocument["_id"]}, profiledocument)

    def update_profile(self, profile) -> None:
        """
        Actualiza el perfil en la base de datos.
        """
        self.profiles.replace_one({"_id": profile.id}, profile.to_dict())

    def insert_avatar(self, Id, value):
        """
        Actualiza el avatar del perfil.
        """
        self.profiles.update_one({"_id": Id}, {"$set": {"user.avatar": value}})


database= Database()
print()