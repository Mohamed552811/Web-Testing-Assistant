"""Load and save the Test Case Designer feature settings (one row in designer_setting)."""

from sqlalchemy import select

from wta.db import Database
from wta.db.designer_models import DesignerSetting
from wta.designer.settings import DesignerSettings

SCOPE = "workspace"
NAME = "feature"


def load(db: Database) -> DesignerSettings:
    with db.Session() as orm:
        row = orm.scalar(select(DesignerSetting).where(DesignerSetting.scope == SCOPE, DesignerSetting.name == NAME))
        return DesignerSettings.from_json(row.value if row else None)


def save(db: Database, settings: DesignerSettings) -> DesignerSettings:
    problems = settings.problems()
    if problems:
        raise ValueError(" ".join(problems))
    value = DesignerSettings.from_json(settings.to_json()).to_json()
    with db.Session() as orm, orm.begin():
        row = orm.scalar(select(DesignerSetting).where(DesignerSetting.scope == SCOPE, DesignerSetting.name == NAME))
        if row is None:
            orm.add(DesignerSetting(scope=SCOPE, name=NAME, value=value))
        else:
            row.value = value
    return DesignerSettings.from_json(value)
