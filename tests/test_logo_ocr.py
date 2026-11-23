from PIL import Image, ImageDraw, ImageFont
from vision import logo_match

def test_mohawk_text_logo_detection():
    img=Image.new("RGB",(850,450),(25,25,25))
    draw=ImageDraw.Draw(img)
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    draw.rectangle((10,10,840,430),outline=(0,230,70),width=18)
    draw.text((75,70),"FOLLOW THE",font=ImageFont.truetype(font,70),fill="white")
    draw.text((95,220),"MOHAWK",font=ImageFont.truetype(font,95),fill=(0,225,70))
    import io
    buffer=io.BytesIO()
    img.save(buffer,format="PNG")
    assert logo_match(buffer.getvalue()) >= 12
