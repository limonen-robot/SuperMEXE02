"""Scrollable notebook content, including mouse wheel over child controls."""
import tkinter as tk
from tkinter import ttk


class ScrollablePage(ttk.Frame):
    def __init__(self, parent, padding=0):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                yscrollincrement=24, xscrollincrement=24)
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self.resize)
        self.canvas.bind('<Configure>', self.resize)
        self.wheel_tag = 'PageWheel' + str(id(self))
        self.bind_class(self.wheel_tag, '<MouseWheel>', self.wheel)
        self.bind_class(self.wheel_tag, '<Shift-MouseWheel>', self.horizontal_wheel)

    def resize(self, _=None):
        width = max(self.content.winfo_reqwidth(), self.canvas.winfo_width())
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self.window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def install_wheel_bindings(self):
        def install(widget):
            tags = widget.bindtags()
            if self.wheel_tag not in tags:
                widget.bindtags((self.wheel_tag,) + tags)
            for child in widget.winfo_children():
                install(child)
        install(self.canvas)

    def wheel(self, event):
        # Lists retain their own scrolling. Other controls scroll the page;
        # notably, the wheel must not silently change a selected motor axis.
        if event.widget.winfo_class() in ('Treeview', 'Text', 'Listbox', 'Scrollbar', 'TScrollbar'):
            return None
        if not event.delta:
            return None
        amount = max(1, abs(event.delta) // 120)
        self.canvas.yview_scroll(-amount if event.delta > 0 else amount, 'units')
        return 'break'

    def horizontal_wheel(self, event):
        if not event.delta:
            return None
        amount = max(1, abs(event.delta) // 120)
        self.canvas.xview_scroll(-amount if event.delta > 0 else amount, 'units')
        return 'break'
