from copy import deepcopy
from threading import RLock


class MemoryStore:
    """Transactional test store; production uses FirestoreStore."""
    def __init__(self):
        self.records = {}
        self.lock = RLock()

    def transaction(self, operation):
        with self.lock:
            records = deepcopy(self.records)

            class Transaction:
                def get(self, collection, key):
                    return deepcopy(records.get((collection, key)))

                def set(self, collection, key, value):
                    records[(collection, key)] = deepcopy(value)

                def update(self, collection, key, value):
                    records[(collection, key)].update(deepcopy(value))

            result = operation(Transaction())
            self.records = records
            return result

    def list(self, collection, field=None, value=None, limit=100):
        with self.lock:
            return [(key, deepcopy(doc)) for (name, key), doc in self.records.items()
                    if name == collection and (field is None or doc.get(field) == value)][:limit]


class FirestoreStore:
    def __init__(self, client):
        self.client = client

    def transaction(self, operation):
        from google.cloud import firestore
        client = self.client

        class Transaction:
            def __init__(self, transaction):
                self.transaction = transaction

            def get(self, collection, key):
                ref = client.collection(collection).document(key)
                snapshot = ref.get(transaction=self.transaction)
                return snapshot.to_dict() if snapshot.exists else None

            def set(self, collection, key, value):
                self.transaction.set(client.collection(collection).document(key), value)

            def update(self, collection, key, value):
                self.transaction.update(client.collection(collection).document(key), value)

        @firestore.transactional
        def execute(transaction):
            return operation(Transaction(transaction))

        return execute(client.transaction())

    def list(self, collection, field=None, value=None, limit=100):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.client.collection(collection)
        if field is not None:
            query = query.where(filter=FieldFilter(field, "==", value))
        return [(doc.id, doc.to_dict()) for doc in query.limit(limit).stream()]
