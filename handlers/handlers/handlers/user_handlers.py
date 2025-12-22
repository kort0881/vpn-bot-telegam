from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from utils.api import parse_key

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("VPN бот запущен. Напиши /key, чтобы получить ключ.")

@router.message(Command("key"))
async def cmd_key(message: Message):
    await parse_key(message)

