from app.db.base import Base, engine
from app.db import checkmk_master_models, fleet_models, models, n2_models  # noqa: F401


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Banco inicializado.")
