import random
import string
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def generate_captcha(length: int = 5):
    # Генерим случайный текст
    text = ''.join(random.choices(string.digits, k=length))

    # Параметры картинки
    width, height = 200, 70
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Можно без шрифта или указать путь к ttf
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    except Exception:
        font = ImageFont.load_default()

    # Рисуем текст примерно по центру
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.text((x, y), text, font=font, fill=(0, 0, 0))


    # Сохраняем в память
    buf = BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)

    # Возвращаем текст и байты PNG
    return text, buf
