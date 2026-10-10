import base64
from io import BytesIO
from PIL import Image
from config.dashgo_conf import PathProj


class AvatarFile:
    @staticmethod
    def save_avatar_file(base64_str: str, img_type: str, user_name: str):
        if not user_name or any(character in user_name for character in '/\\:') or user_name in ('.', '..'):
            raise ValueError('Invalid avatar file name')
        if len(base64_str) > 8 * 1024 * 1024:
            raise ValueError('Avatar exceeds size limit')
        file_like = BytesIO(base64.b64decode(base64_str, validate=True))
        pil_img = Image.open(file_like)
        if pil_img.width * pil_img.height > 16_000_000:
            raise ValueError('Avatar dimensions exceed limit')
        image_rgb = pil_img.convert('RGB')
        image_rgb = image_rgb.resize((256, 256))
        image_rgb.save(PathProj.AVATAR_DIR_PATH / f'{user_name}.jpg', quality=50)
