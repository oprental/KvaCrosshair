"""Exercise real palette interactions and cancellation with isolated app data."""
from pathlib import Path
import sys,tempfile,math
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from color_picker import ColorPicker
from PIL import ImageGrab

with tempfile.TemporaryDirectory() as folder:
    main.DATA=Path(folder)
    app=main.Application()
    try:
        app.root.update()
        picker=ColorPicker(app.root,'#000000',nickname='Player')
        picker.hex.set('#12abcd')
        assert picker.preview.cget('fg')=='#12abcd'
        assert math.isclose(picker.h,.53,abs_tol=.03)
        picker.pick_hue(SimpleNamespace(x=0))
        picker.pick_square(SimpleNamespace(x=319,y=0))
        assert picker.hex.get()=='#ff0000'
        picker.pick_square(SimpleNamespace(x=-20,y=300))
        assert picker.hex.get()=='#000000'
        picker.hex.set('invalid')
        assert str(picker.select.cget('state'))=='disabled'
        picker.finish(True)
        assert picker.window.winfo_exists()
        picker.hex.set('#9A64FF')
        app.root.update()
        w=picker.window
        ImageGrab.grab(bbox=(w.winfo_rootx(),w.winfo_rooty(),w.winfo_rootx()+w.winfo_width(),w.winfo_rooty()+w.winfo_height())).save('build/pro-palette-preview.png')
        picker.finish(True)
        assert picker.result=='#9a64ff'
        canceled=ColorPicker(app.root,'#9a64ff')
        canceled.hex.set('#123456');canceled.finish(False)
        assert canceled.result is None
        print('Purple palette: HSV, HEX, validation, accept and cancel OK')
    finally:app.close()
