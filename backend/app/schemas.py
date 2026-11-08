from typing import Literal
from pydantic import BaseModel

Status = Literal['new','confirmed','assembling','shipping','completed','cancelled']


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: Literal['user','operator']


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal['bearer']
    expires_in: int
    user: UserOut


class ProductOut(BaseModel):
    id: str
    name: str
    description: str
    sku: str
    category: str
    price: int
    stock: int
    art: str
    badge: str


class CatalogOut(BaseModel):
    items: list[ProductOut]


class OrderItemOut(BaseModel):
    product_id: str
    name: str
    price: int
    quantity: int


class HistoryOut(BaseModel):
    id: int
    status: Status
    created_at: str


class OrderOut(BaseModel):
    id: str
    number: int
    user_id: str
    status: Status
    version: int
    total: int
    delivery_fee: int
    name: str
    phone: str
    address: str
    delivery: Literal['pickup','courier']
    comment: str
    channel: Literal['web','mobile']
    created_at: str
    updated_at: str
    items: list[OrderItemOut]
    history: list[HistoryOut]


class OrdersOut(BaseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: object | None = None
    request_id: str


class ErrorOut(BaseModel):
    error: ErrorDetail
