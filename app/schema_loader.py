"""
Schema loader: reads schema.json (generated from the Excel file) and seeds ModuleConfig.
"""
import json
import re
import os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ModuleConfig

SCHEMA_PATH = os.getenv("SCHEMA_PATH", "/app/schema.json")

# Map sheet names to URL-safe slugs and icons
_SLUG_MAP = {
    "Заказы": ("orders", "bi-clipboard-check"),
    "Гарантийное обслуживание": ("warranty", "bi-shield-check"),
    "Клиенты": ("clients", "bi-people"),
    "Финансы": ("finance", "bi-cash-stack"),
    "Расходники": ("supplies", "bi-box-seam"),
    "Аналитика": ("analytics", "bi-graph-up"),
}


def _make_slug(name: str) -> str:
    if name in _SLUG_MAP:
        return _SLUG_MAP[name][0]
    slug = re.sub(r'[^a-zA-Zа-яА-Я0-9]+', '_', name).strip('_').lower()
    # transliterate basic cyrillic
    tr = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh',
          'з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o',
          'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
          'ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'}
    result = ""
    for ch in slug:
        result += tr.get(ch, ch)
    return result


def _get_icon(name: str) -> str:
    if name in _SLUG_MAP:
        return _SLUG_MAP[name][1]
    return "bi-table"


def load_schema() -> dict:
    path = Path(SCHEMA_PATH)
    if not path.exists():
        # Try relative path
        alt = Path(__file__).parent.parent / "schema.json"
        if alt.exists():
            path = alt
        else:
            return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def seed_modules(db: AsyncSession):
    """Create ModuleConfig rows from schema if they don't exist."""
    schema = load_schema()
    if not schema:
        return

    for idx, (sheet_name, fields) in enumerate(schema.items()):
        slug = _make_slug(sheet_name)
        result = await db.execute(select(ModuleConfig).where(ModuleConfig.slug == slug))
        existing = result.scalar_one_or_none()
        if not existing:
            mc = ModuleConfig(
                slug=slug,
                sheet_name=sheet_name,
                display_name=sheet_name,
                icon=_get_icon(sheet_name),
                enabled=True,
                fields_schema=fields,
                sort_order=idx,
            )
            db.add(mc)
    await db.commit()


async def get_all_modules(db: AsyncSession) -> list[ModuleConfig]:
    result = await db.execute(
        select(ModuleConfig).where(ModuleConfig.enabled == True).order_by(ModuleConfig.sort_order)
    )
    return list(result.scalars().all())


async def get_module_by_slug(db: AsyncSession, slug: str) -> ModuleConfig | None:
    result = await db.execute(select(ModuleConfig).where(ModuleConfig.slug == slug))
    return result.scalar_one_or_none()
