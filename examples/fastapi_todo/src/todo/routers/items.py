from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/items", tags=["items"])

_ITEMS: dict[int, "Item"] = {}


class Item(BaseModel):
    id: int
    title: str = Field(min_length=1, max_length=100)
    done: bool = False


@router.get("/")
def list_items() -> list[Item]:
    return list(_ITEMS.values())


@router.post("/", status_code=201)
def create_item(item: Item) -> Item:
    if item.id in _ITEMS:
        raise HTTPException(status_code=409, detail="Item already exists")
    _ITEMS[item.id] = item
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int) -> None:
    if _ITEMS.pop(item_id, None) is None:
        raise HTTPException(status_code=404, detail="Item not found")
