import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.dispatch import _reserve_node_slot
from app.database import Base
from app.models.compute_node import ComputeNode


class DispatchCapacityTests(unittest.TestCase):
    def test_reserves_only_one_slot_when_node_capacity_is_one(self) -> None:
        engine = create_engine("sqlite://")
        session = sessionmaker(bind=engine)()
        try:
            Base.metadata.create_all(engine)
            session.add(
                ComputeNode(
                    id="node-linux-01",
                    display_name="Linux GPU Node 01",
                    max_concurrent_jobs=1,
                    running_jobs=0,
                )
            )
            session.commit()

            self.assertTrue(_reserve_node_slot(session, "node-linux-01"))
            self.assertFalse(_reserve_node_slot(session, "node-linux-01"))
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
