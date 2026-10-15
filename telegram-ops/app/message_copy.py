"""Validated private-message text, formatting, and stored image attachments."""
import hashlib
import html
import io
import warnings
from html.parser import HTMLParser
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from PIL import Image, ImageOps
from telethon.extensions import html as telegram_html
from app.database import get_db
from app.relay_models import MessageAsset
from app.relay_logic import render_copy

router = APIRouter()
MAX_UPLOAD = 5 * 1024 * 1024


class CopyInput(BaseModel):
    template: str = Field(default="", max_length=12000)
    format: str = Field(default="plain", pattern="^(plain|html)$")
    image_id: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")


class SafeHtml(HTMLParser):
    aliases = {"strong": "b", "em": "i", "s": "del", "strike": "del"}
    allowed = {"b", "i", "u", "del", "code", "a"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        tag = self.aliases.get(tag, tag)
        if tag == "br" and not attrs:
            self.parts.append("\n")
            return
        if tag not in self.allowed:
            raise ValueError("文案仅支持加粗、斜体、下划线、删除线、等宽文字和链接")
        if tag == "a":
            if len(attrs) != 1 or attrs[0][0] != "href" or "a" in self.stack:
                raise ValueError("链接格式不正确")
            url = attrs[0][1] or ""
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() or ord(c)<32 for c in url):
                raise ValueError("链接请填写完整 http:// 或 https:// 地址")
            self.parts.append('<a href="' + html.escape(url, quote=True) + '">')
        else:
            if attrs:
                raise ValueError("文字格式包含不支持的属性")
            self.parts.append('<' + tag + '>')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = self.aliases.get(tag, tag)
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("文字格式没有正确闭合，请检查标签")
        self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def handle_comment(self, data):
        raise ValueError("文案不支持 HTML 注释")

    def handle_decl(self, decl):
        raise ValueError("文案格式不正确")


def prepare_copy(template, options=None, username="example_user"):
    options = options or {}
    source = render_copy(template, username)
    if options.get("format", "plain") == "html":
        parser = SafeHtml()
        parser.feed(source)
        parser.close()
        if parser.stack:
            raise ValueError("文字格式没有正确闭合，请检查标签")
        markup = ''.join(parser.parts)
        text, entities = telegram_html.parse(markup)
    else:
        text, entities, markup = source, [], html.escape(source)
    size = len(text.encode('utf-16-le')) // 2
    limit = 1024 if options.get('image_id') else 3500
    if size > limit:
        raise ValueError(f"文案过长：当前 {size}，上限 {limit}；表情可能占两个字符")
    if not text.strip():
        raise ValueError("请填写私信文字")
    return text, entities, markup


def validate_copy(db, template, options):
    prepare_copy(template, options, username='u' * 32)
    if options.get('image_id') and not db.get(MessageAsset, options['image_id']):
        raise ValueError("配图不存在，请重新上传")


def normalize_image(data):
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("请选择 5 MB 以内的 JPG、PNG 或 WebP 图片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in ('JPEG','PNG','WEBP') or original.width * original.height > 20_000_000:
                    raise ValueError("请选择 2000 万像素以内的 JPG、PNG 或 WebP 图片")
                if getattr(original, 'is_animated', False):
                    raise ValueError("当前支持静态图片，请上传 JPG、PNG 或静态 WebP")
                original.load()
                picture = ImageOps.exif_transpose(original).convert('RGBA')
                picture.thumbnail((2560,2560))
                background = Image.new('RGB', picture.size, 'white')
                background.paste(picture, mask=picture.getchannel('A'))
                output = io.BytesIO()
                background.save(output,format='JPEG',quality=90)
                if max(picture.size) > min(picture.size) * 20:
                    raise ValueError("图片过于狭长，请使用长宽比不超过 20 的图片")
                return output.getvalue(), picture.width, picture.height
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("图片读取失败，请重新选择有效图片") from exc


@router.post('/api/message-assets')
async def upload_asset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        data = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    try:
        data, width, height = normalize_image(data)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    asset_id = hashlib.sha256(data).hexdigest()
    if not db.get(MessageAsset, asset_id):
        total = db.query(func.sum(func.length(MessageAsset.data))).scalar() or 0
        if total + len(data) > 100 * 1024 * 1024:
            raise HTTPException(422,"图片素材空间已满，请联系管理员整理素材")
        db.add(MessageAsset(id=asset_id,data=data,content_type='image/jpeg'))
        db.commit()
    return dict(id=asset_id,url='/api/message-assets/'+asset_id,width=width,height=height)


@router.get('/api/message-assets/{id}')
def asset_image(id: str, db: Session = Depends(get_db)):
    asset = db.get(MessageAsset, id)
    if not asset:
        raise HTTPException(404,'图片不存在')
    return Response(asset.data,media_type=asset.content_type,headers={'X-Content-Type-Options':'nosniff'})


@router.post('/api/message-copy/preview')
def preview_copy(data: CopyInput, db: Session = Depends(get_db)):
    options = data.model_dump(exclude={'template'})
    try:
        validate_copy(db,data.template,options)
        text, _, markup = prepare_copy(data.template,options)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return dict(html=markup,text=text,image_url='/api/message-assets/'+data.image_id if data.image_id else None)
