"""Borderless HSV palette using the application's purple theme."""
import colorsys
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from subscriptions import normalize_color


class ColorPicker:
    def __init__(self,parent,color,title='Цвет ника KVA PRO',nickname='Твой ник'):
        from main import BG,FIELD,MUTED,TEXT,ACCENT
        self.result=None
        self.h,self.s,self.v=colorsys.rgb_to_hsv(*(int(color[i:i+2],16)/255 for i in (1,3,5)))
        self.window=tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.transient(parent)
        self.window.configure(bg=ACCENT)
        body=tk.Frame(self.window,bg=BG)
        body.pack(padx=1,pady=1,fill='both',expand=True)
        header=tk.Frame(body,bg=BG)
        header.pack(fill='x',padx=20,pady=(16,10))
        heading=tk.Label(header,text=title,bg=BG,fg=TEXT,font=('Segoe UI',12,'bold'))
        heading.pack(side='left')
        ttk.Button(header,text='×',width=3,command=lambda:self.finish(False)).pack(side='right')
        for widget in (header,heading):
            widget.bind('<ButtonPress-1>',self.drag_start)
            widget.bind('<B1-Motion>',self.drag_move)
        content=tk.Frame(body,bg=BG)
        content.pack(padx=22,pady=(0,20))
        tk.Label(content,text='Выбери насыщенность и яркость',bg=BG,fg=MUTED,font=('Segoe UI',9)).pack(anchor='w',pady=(0,8))
        self.square=tk.Canvas(content,width=320,height=190,highlightthickness=0,cursor='crosshair')
        self.square.pack()
        self.square.bind('<Button-1>',self.pick_square)
        self.square.bind('<B1-Motion>',self.pick_square)
        tk.Label(content,text='Оттенок',bg=BG,fg=MUTED,font=('Segoe UI',9)).pack(anchor='w',pady=(12,6))
        self.hue=tk.Canvas(content,width=320,height=22,highlightthickness=0,cursor='hand2')
        self.hue.pack()
        spectrum=Image.new('RGB',(320,22))
        for x in range(320):
            rgb=tuple(round(v*255) for v in colorsys.hsv_to_rgb(x/319,1,1))
            for y in range(22):spectrum.putpixel((x,y),rgb)
        self.hue_image=ImageTk.PhotoImage(spectrum,master=self.window)
        self.hue.create_image(0,0,anchor='nw',image=self.hue_image)
        self.hue.bind('<Button-1>',self.pick_hue)
        self.hue.bind('<B1-Motion>',self.pick_hue)
        row=tk.Frame(content,bg=BG)
        row.pack(fill='x',pady=(16,8))
        self.preview=tk.Label(row,text=nickname,bg=FIELD,font=('Segoe UI',11,'bold'),padx=12,pady=10)
        self.preview.pack(side='left',fill='x',expand=True)
        self.hex=tk.StringVar(value=color)
        self.entry=ttk.Entry(row,textvariable=self.hex,width=10)
        self.entry.pack(side='right',padx=(12,0))
        self.error=tk.Label(content,text='HEX · #RRGGBB',bg=BG,fg=MUTED,font=('Segoe UI',9))
        self.error.pack(anchor='w')
        actions=tk.Frame(content,bg=BG)
        actions.pack(fill='x',pady=(15,0))
        ttk.Button(actions,text='Отмена',command=lambda:self.finish(False)).pack(side='left')
        self.select=ttk.Button(actions,text='Выбрать цвет',style='Accent.TButton',command=lambda:self.finish(True))
        self.select.pack(side='right')
        self.hex.trace_add('write',self.hex_changed)
        self.render_square()
        self.update_markers()
        self.preview.configure(fg=color)
        self.window.bind('<Escape>',lambda _:self.finish(False))
        self.window.bind('<Return>',lambda _:self.finish(True))
        self.window.update_idletasks()
        width,height=self.window.winfo_reqwidth(),self.window.winfo_reqheight()
        x=max(0,parent.winfo_rootx()+(parent.winfo_width()-width)//2)
        y=max(0,parent.winfo_rooty()+(parent.winfo_height()-height)//2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        self.window.deiconify()
        self.window.lift()
        self.window.grab_set()
        self.entry.focus_set()

    def drag_start(self,event):
        self.drag=(event.x_root-self.window.winfo_x(),event.y_root-self.window.winfo_y())

    def drag_move(self,event):
        self.window.geometry(f'+{event.x_root-self.drag[0]}+{event.y_root-self.drag[1]}')

    def render_square(self):
        image=Image.new('RGB',(320,190))
        pixels=image.load()
        for y in range(190):
            for x in range(320):pixels[x,y]=tuple(round(v*255) for v in colorsys.hsv_to_rgb(self.h,x/319,1-y/189))
        self.square_image=ImageTk.PhotoImage(image,master=self.window)
        self.square.delete('all')
        self.square.create_image(0,0,anchor='nw',image=self.square_image)

    def update_markers(self):
        self.square.delete('marker');self.hue.delete('marker')
        x,y=self.s*319,(1-self.v)*189
        self.square.create_oval(x-6,y-6,x+6,y+6,outline='#111111',width=4,tags='marker')
        self.square.create_oval(x-6,y-6,x+6,y+6,outline='#ffffff',width=2,tags='marker')
        x=self.h*319
        self.hue.create_rectangle(x-3,1,x+3,21,outline='#ffffff',width=2,tags='marker')

    def hex_changed(self,*_):
        try:color=normalize_color(self.hex.get().strip())
        except ValueError:
            self.error.configure(text='Введи 6 цифр цвета: #RRGGBB')
            self.select.configure(state='disabled')
            return
        h,s,v=colorsys.rgb_to_hsv(*(int(color[i:i+2],16)/255 for i in (1,3,5)))
        if v==0 or s==0:h=self.h
        changed=abs(h-self.h)>.00001
        self.h,self.s,self.v=h,s,v
        if changed:self.render_square()
        self.update_markers()
        self.preview.configure(fg=color)
        self.error.configure(text='HEX · #RRGGBB')
        self.select.configure(state='normal')

    def update_color(self):
        rgb=tuple(round(v*255) for v in colorsys.hsv_to_rgb(self.h,self.s,self.v))
        self.hex.set('#' + ''.join(f'{v:02x}' for v in rgb))

    def pick_square(self,event):
        self.s=max(0,min(1,event.x/319));self.v=max(0,min(1,1-event.y/189))
        self.update_color()

    def pick_hue(self,event):
        self.h=max(0,min(1,event.x/319))
        self.render_square()
        self.update_color()

    def finish(self,accept):
        if accept:
            try:self.result=normalize_color(self.hex.get().strip())
            except ValueError:return
        self.window.grab_release()
        self.window.destroy()


def ask_color(parent,color,nickname='Твой ник'):
    picker=ColorPicker(parent,color,nickname=nickname)
    parent.wait_window(picker.window)
    return picker.result
