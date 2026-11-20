"""Screen-driven Wartune runner. No recording timing or account passwords in logs."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    pass

import cv2
import numpy as np
from PIL import Image, ImageGrab, ImageOps
import pytesseract
from pynput import keyboard
from desktop_support import find_app_window, TelegramClient, protect_secret, RecorderInstance
from windows_mouse import WindowsMouse
from smart_bot_recovery import SmartRecovery

BASE = Path(__file__).resolve().parent
ASSETS = BASE / 'smart_bot_assets'
RUNS = BASE / 'smart_bot_runs'
GAME = Path(r'C:\Program Files (x86)\7Road\wan\wartune\client2.exe')
TESSERACT = Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
# Launcher coordinates are relative to its native 844 x 584 window.
REGIONS = {
    'R2-US West': (668,253,630,237), 'R2-US East': (347,295,309,280),
    'R2-Europe': (440,295,410,280), 'R2-Oceanic': (531,295,500,280),
    'Proficient Ct': (347,253,310,237), 'Kabam': (427,253,411,237),
    'Kongregate': (509,253,470,237), 'Armor': (590,253,573,237),
    'Ennia': (610,295,593,280), 'Koramgame': (687,295,650,280),
    'Agame': (346,337,310,323),
}


def parse_server_number(text):
    match=re.search(r'[sS$]\s*(\d{1,6})\b',text)
    return int(match.group(1)) if match else None


def progress_key(account,region,server,code):
    return f'{account}|{region}|{server}|{code}'


def was_completed(progress,account,region,server,code):
    key=progress_key(account,region,server,code)
    if progress.get(key,{}).get('status')=='complete':return True
    # Migrate only the original verified region; identical server numbers elsewhere are different.
    return region=='R2-US West' and progress.get(f'{account}|{server}|{code}',{}).get('status')=='complete'


def account_range(prefix, start, end):
    if not re.fullmatch(r'[A-Za-z0-9_\-]+', prefix):
        raise ValueError('Use letters, numbers, underscores or hyphens in the account prefix.')
    if min(start, end) < 1:
        raise ValueError('Account numbers must be positive.')
    return [f'{prefix}{i}' for i in range(start, end + (1 if end >= start else -1), 1 if end >= start else -1)]


def select_accounts(prefix, start, end, listed=None):
    requested=account_range(prefix,start,end)
    if listed is None:
        return requested,[]
    known=set(listed)
    return [a for a in requested if a in known],[a for a in requested if a not in known]


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    tmp.replace(path)


def save_password(password):
    (BASE / '.smart_bot_password').write_bytes(protect_secret(password.encode()))


def load_password():
    p = BASE / '.smart_bot_password'
    return protect_secret(p.read_bytes(), decrypt=True).decode() if p.exists() else ''


@dataclass
class Frame:
    image: Image.Image
    handle: int
    bounds: tuple
    time: float


class Desktop:
    def __init__(self, stop, executable=GAME):
        self.stop = stop
        self.executable = Path(executable)
        self.mouse = WindowsMouse()
        self.keys = keyboard.Controller()
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        for name, args, result in [
            ('GetWindowRect', [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ('SetForegroundWindow', [wintypes.HWND], wintypes.BOOL),
            ('GetForegroundWindow', [], wintypes.HWND),
            ('ShowWindow', [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            ('IsIconic', [wintypes.HWND], wintypes.BOOL),
            ('PostMessageW', [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            ('MoveWindow', [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL], wintypes.BOOL),
        ]:
            fn = getattr(self.api, name); fn.argtypes = args; fn.restype = result
        self.placed = set()

    def check(self):
        if (BASE / '.smart_bot_stop').exists():
            self.stop.set()
        if self.stop.is_set():
            raise InterruptedError('Stopped by user (F9).')

    def close(self):
        handle = find_app_window(self.executable)
        if handle:
            deadline = time.monotonic() + 8
            sent=set()
            while (handle := find_app_window(self.executable)):
                if handle not in sent:
                    self.api.PostMessageW(handle, 0x10, 0, 0)
                    sent.add(handle)
                self.check()
                if time.monotonic() > deadline:
                    raise RuntimeError('Wartune did not close. Resolve its close dialog, then resume.')
                self.stop.wait(.15)

    def launch(self):
        if not self.executable.is_file():
            raise ValueError(f'Game executable not found: {self.executable}')
        if not find_app_window(self.executable):
            subprocess.Popen([str(self.executable)], cwd=str(self.executable.parent))

    def capture(self):
        self.check()
        handle = find_app_window(self.executable)
        if not handle:
            return None
        if self.api.IsIconic(handle):
            self.api.ShowWindow(handle, 9)
        if self.api.GetForegroundWindow() != handle:
            self.api.SetForegroundWindow(handle)
            self.stop.wait(.08)
        if self.api.GetForegroundWindow() != handle:
            raise RuntimeError('Wartune cannot receive focus. Click the game and resume.')
        r = wintypes.RECT()
        if not self.api.GetWindowRect(handle, ctypes.byref(r)):
            raise OSError('Cannot locate Wartune window.')
        w, h = r.right-r.left, r.bottom-r.top
        # Preserve the proven game size; launcher keeps its own native dimensions.
        key = (handle, w, h)
        if key not in self.placed:
            self.api.MoveWindow(handle, 416, 185, w, h, True)
            self.placed.add(key)
            self.api.GetWindowRect(handle, ctypes.byref(r))
        bounds = (r.left, r.top, r.right, r.bottom)
        return Frame(ImageGrab.grab(bbox=bounds, all_screens=True), handle, bounds, time.monotonic())

    def verify(self, frame):
        self.check()
        if time.monotonic()-frame.time > 4:
            raise RuntimeError('Refused input based on an old screenshot.')
        if self.api.GetForegroundWindow() != frame.handle:
            raise RuntimeError('Focus changed; stopped before clicking another application.')
        r = wintypes.RECT(); self.api.GetWindowRect(frame.handle, ctypes.byref(r))
        if (r.left, r.top, r.right, r.bottom) != frame.bounds:
            raise RuntimeError('Game moved during recognition; resume with stable placement.')

    def click(self, frame, x, y):
        self.verify(frame)
        if not (0 <= x < frame.image.width and 25 <= y < frame.image.height):
            raise ValueError('Click outside the game content.')
        self.mouse.move_to(frame.bounds[0]+int(x), frame.bounds[1]+int(y), self.stop)
        self.mouse.press('left')
        try:
            self.stop.wait(.035)
        finally:
            self.mouse.release('left')

    def key(self, frame, key):
        self.verify(frame)
        self.keys.press(key); self.keys.release(key)

    def fill(self, frame, x, y, text):
        self.click(frame, x, y)
        self.stop.wait(.06)
        self.verify(frame)
        with self.keys.pressed(keyboard.Key.ctrl):
            self.keys.press('a'); self.keys.release('a')
        self.keys.type(text)
        self.stop.wait(.18)  # Flash paints text on its next frame, after input is queued.


class Vision:
    def __init__(self):
        if not TESSERACT.exists():
            raise RuntimeError('Tesseract OCR is required. See SMART_BOT.md.')
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT)
        os.environ['TESSDATA_PREFIX'] = str(ASSETS / 'tessdata')
        self.templates = {}
        for p in ASSETS.glob('*.png'):
            self.templates[p.stem] = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)

    def text(self, frame, box=None, psm=6, scale=2):
        im = frame.image.crop(box) if box else frame.image
        im = ImageOps.grayscale(im)
        if scale != 1:
            im = im.resize((int(im.width*scale), int(im.height*scale)))
        return pytesseract.image_to_string(im, config=f'--psm {psm}', timeout=4).strip()

    def locate(self, frame, name, box=None, threshold=.87):
        target = self.templates.get(name)
        if target is None:
            return None
        source = np.asarray(ImageOps.grayscale(frame.image.crop(box) if box else frame.image))
        if source.shape[0] < target.shape[0] or source.shape[1] < target.shape[1]:
            return None
        _, score, _, pos = cv2.minMaxLoc(cv2.matchTemplate(source, target, cv2.TM_CCOEFF_NORMED))
        if score < threshold:
            return None
        return (pos[0]+target.shape[1]//2+(box[0] if box else 0), pos[1]+target.shape[0]//2+(box[1] if box else 0))

    def character_name_matches(self, frame, username):
        # Keep the field border out of OCR: it turned demo59 into demoS9.
        box = (817,543,967,568)
        def matches(text):
            # OCR can append the blinking caret. Never substitute letters/digits
            # (for example S/5), which could accept a different account name.
            return text.strip().rstrip('|').strip().casefold() == username.casefold()
        if matches(self.text(frame, box, psm=7, scale=3)):
            return True
        # Isolate the yellow letters from the dark field and blinking caret.
        rgb = np.asarray(frame.image.crop(box))
        letters = (rgb[:,:,0] > 140) & (rgb[:,:,1] > 130) & (rgb[:,:,2] < 120)
        ink = Image.fromarray(np.where(letters, 0, 255).astype('uint8'))
        ink = ImageOps.expand(ink, border=6, fill=255)
        ink = ink.resize((ink.width*3, ink.height*3))
        text = pytesseract.image_to_string(ink, config='--psm 7', timeout=4)
        return matches(text)


class Bot(SmartRecovery):
    def __init__(self, stop=None, report=print, executable=GAME, region='R2-US West', server=1218):
        if region not in REGIONS:raise ValueError('Select a supported launcher region.')
        if server!='first' and (not str(server).isdigit() or int(server)<1):raise ValueError('Enter a positive server number.')
        self.region=region
        self.server_choice=server
        self.server=int(server) if server!='first' else None
        self.stop = stop or threading.Event()
        self.report = report
        self.desktop = Desktop(self.stop, executable)
        self.vision = Vision()
        self.stage = ''
        self.run_dir = None
        self.frame = None
        self.account = ''
        self.last_action = {}
        self.started = 0
        self.last_text = ''
        self.escape_key=keyboard.Key.esc
        self.checkpoint={}

    def log(self, message):
        line = f'[{time.monotonic()-self.started:6.1f}s] {message}'
        self.report(line)
        if self.run_dir:
            with (self.run_dir/'run.log').open('a', encoding='utf-8') as f:
                f.write(line+'\n')

    def enter(self, stage):
        self.stage = stage
        self.log(stage)
        self.stage_start = time.monotonic()

    def snapshot(self):
        self.desktop.check()
        frame = self.desktop.capture()
        self.frame = frame
        if frame and self.run_dir:
            frame.image.save(self.run_dir/'latest.png')
        return frame

    def click(self, frame, x, y, label, cooldown=.65):
        now = time.monotonic()
        if now-self.last_action.get(label, 0) < cooldown:
            return False
        self.desktop.click(frame, x, y)
        self.last_action[label] = now
        self.log(label)
        self.stop.wait(.15)
        return True

    def wait(self, description, predicate, timeout=40):
        self.enter(description)
        while time.monotonic()-self.stage_start < timeout:
            f = self.snapshot()
            if f:
                value = predicate(f)
                if value:
                    return value
            self.stop.wait(.15)
        raise TimeoutError(f'Timed out at {description}. Last OCR: {self.last_text[:250]}')

    def read(self, f, box=None, psm=6, scale=2):
        self.last_text = self.vision.text(f, box, psm, scale)
        if self.run_dir:
            (self.run_dir/'ocr.txt').write_text(self.last_text,encoding='utf-8')
        return self.last_text.lower()

    def wait_text(self, description, box, pattern, timeout=30):
        return self.wait(description, lambda f: f if re.search(pattern, self.read(f, box)) else None, timeout)

    def login(self, username, password):
        self.desktop.close()
        self.desktop.launch()
        def launcher(f):
            t = self.read(f, (int(f.image.width*.35), 75, f.image.width-10, f.image.height-5))
            if 'last played' in t or 'all servers' in t:
                raise RuntimeError('Launcher retained a different login; log out before starting.')
            return f if 'password' in t and ('login' in t or 'remember' in t) else None
        f = self.wait('Waiting for login', launcher, 45)
        # Launcher layout scales with its window size, unlike the Flash game.
        sx, sy = f.image.width/830, f.image.height/572
        self.desktop.fill(f, 469*sx, 363*sy, username)
        f = self.snapshot(); self.desktop.fill(f, 469*sx, 432*sy, password)
        f = self.snapshot(); self.click(f, 710*sx, 361*sy, 'Login')
        def server_list(f):
            t = self.read(f, (290, 85, f.image.width-15, f.image.height-15), psm=11)
            if 'incorrect' in t or 'wrong password' in t:
                raise RuntimeError('Login rejected.')
            return f if 'all servers' in t else None
        self.wait('Waiting for server list',server_list,45)
        f=self.select_region(self.region)
        f,x,y,number=self.select_server(self.server_choice)
        self.server=number
        f.image.save(self.run_dir/'server.png')
        self.click(f,x,y,f'Enter {self.region} server {number}')
        self.wait('Waiting for game', lambda f: f if f.image.size == (1088,671) else None, 60)

    def region_selected(self,f,region):
        _,_,px,py=REGIONS[region]
        sx,sy=f.image.width/844,f.image.height/584
        sample=np.array(f.image.crop((int(px*sx),int(py*sy),int((px+4)*sx),int((py+4)*sy)))).mean(axis=(0,1))
        return sample[2]-sample[0]>28 and sample[1]-sample[0]>7

    def select_region(self,region):
        if region not in REGIONS:raise ValueError('Unknown region')
        before=self.snapshot()
        changed=not self.region_selected(before,region)
        def first_label(f):
            sx,sy=f.image.width/844,f.image.height/584
            box=tuple(int(v) for v in (330*sx,389*sy,493*sx,412*sy))
            return self.read(f,box,psm=7,scale=3)
        old_label=first_label(before) if changed else None
        def selected(f):
            if self.region_selected(f,region):return f
            x,y,_,_=REGIONS[region]
            if self.click(f,x*f.image.width/844,y*f.image.height/584,'Select region '+region,1.5):
                self.desktop.mouse.move_to(f.bounds[0]+20,f.bounds[1]+20,self.stop)
            return None
        f=self.wait('Selecting region '+region,selected,15)
        if changed:
            previous=[None]
            def new_list(f):
                label=first_label(f)
                valid=label!=old_label and parse_server_number(label) is not None
                stable=valid and label==previous[0]
                previous[0]=label
                return f if stable else None
            self.wait('Waiting for the selected region server list',new_list,20)
        return self.snapshot()

    def visible_servers(self,f):
        sx,sy=f.image.width/844,f.image.height/584
        entries=[]
        for row in range(3):
            for column in range(2):
                x,y=330+200*column,389+39*row
                box=tuple(int(v) for v in (x*sx,y*sy,(x+163)*sx,(y+23)*sy))
                text=self.read(f,box,psm=7,scale=3)
                number=parse_server_number(text)
                if number:entries.append((number,(x+65)*sx,(y+11)*sy,text))
        return entries

    def select_server(self,choice):
        def found(f):
            if not self.region_selected(f,self.region):raise RuntimeError('Selected region is no longer active.')
            if choice=='first':
                sx,sy=f.image.width/844,f.image.height/584
                box=tuple(int(v) for v in (330*sx,389*sy,493*sx,412*sy))
                text=self.read(f,box,psm=7,scale=3)
                number=parse_server_number(text)
                entries=[(number,395*sx,400*sy,text)] if number else []
            else:
                entries=self.visible_servers(f)
            if not entries:return None
            # First means the first visible top-left entry, never Last Played.
            target=entries[0] if choice=='first' else next((e for e in entries if e[0]==int(choice)),None)
            if target:
                number,x,y,_=target
                # A scan may exceed the input freshness limit. Re-capture and verify the exact row.
                fresh=self.snapshot()
                if not self.region_selected(fresh,self.region):return None
                box=(int(x-65*f.image.width/844),int(y-11*f.image.height/584),int(x+98*f.image.width/844),int(y+12*f.image.height/584))
                if parse_server_number(self.read(fresh,box,psm=7,scale=3))!=number:return None
                return fresh,x,y,number
            return None
        return self.wait('Selecting '+('first server' if choice=='first' else f'server {choice}'),found,20)

    def tutorial(self, username):
        self.enter('Tutorial to level 11')
        deadline = time.monotonic()+360
        last_ocr = 0
        character_named = False
        while time.monotonic() < deadline:
            f = self.snapshot()
            if not f:
                self.stop.wait(.15); continue
            if f.image.size != (1088,671):
                raise RuntimeError(f'Unexpected game size {f.image.size}; expected 1088 x 671.')
            if self.vision.locate(f,'worg_challenge',(310,470,490,590),.9):
                f.image.save(self.run_dir/'level11.png');return
            if self.vision.locate(f,'create_start',(745,590,930,650),.86):
                self.character_creation(f,username,character_named)
                character_named=True
                self.stop.wait(.2); continue
            point=self.vision.locate(f,'tutorial_ok',(300,200,850,570),.92)
            if point:
                self.click(f,*point,'Accept tutorial notice');continue
            if self.vision.locate(f,'city_complete',(830,260,1010,315),.86):
                self.click(f,900,290,'Complete Reclaim the City');continue
            if self.barracks_visible(f):
                self.finish_city();return
            if self.vision.locate(f,'campaign_prompt',(500,470,930,625),.88):
                self.enter_campaign(f);return
            # These template matches take milliseconds; countdown prompts do not wait on OCR.
            point = self.vision.locate(f, 'dialogue', (595,630,830,670), .78)
            if point:
                self.click(f, 690,654,'Continue dialogue',.45)
                self.stop.wait(.12); continue
            point = self.vision.locate(f, 'intro', (450,370,740,480), .88)
            if point:
                self.click(f,*point,'Continue introduction'); continue
            point = self.vision.locate(f, 'learn_skill', (480,270,620,420), .91)
            if point:
                self.click(f,*point,'Learn Ultimate Slasher'); continue
            if self.vision.locate(f,'use_ultimate',(185,470,620,555),.84):
                self.click(f,433,640,'Use Ultimate Slasher'); continue
            if self.vision.locate(f,'use_slasher',(185,470,620,555),.84):
                self.click(f,387,640,'Use Slasher'); continue
            if time.monotonic()-last_ocr < .4:
                self.stop.wait(.08); continue
            last_ocr = time.monotonic()
            if self.vision.locate(f,'open_barracks',(420,70,810,180),.84):
                self.finish_city()
                return
            # Read only useful game regions, arranged in one image to avoid process startup per crop.
            regions = [(290,170,850,450),(170,465,1035,670),(830,235,1080,320),(75,110,120,145)]
            strip = Image.new('RGB',(865,605),'black')
            offset=0
            for box in regions:
                part=f.image.crop(box); strip.paste(part,(0,offset)); offset+=part.height
            proxy=Frame(strip,f.handle,f.bounds,f.time)
            t=self.read(proxy,psm=11,scale=1.5)
            if 'worg lair' in t and ('challenge' in t or 'clear out' in t):
                f.image.save(self.run_dir/'level11.png'); return
            if 'character' in t and ('name' in t or 'knight' in t) or 'start game' in t:
                self.character_creation(f, username, character_named)
                character_named=True
                continue
            if 'click to continue' in t:
                self.click(f,690,654,'Continue dialogue',.45)
            elif 'special attack' in t or 'skill below' in t:
                self.click(f,387,640,'Use Slasher')
            elif 'ultimate slasher' in t and ('use' in t or 'click' in t):
                self.click(f,441,641,'Use Ultimate Slasher')
            elif 'reclaim' in t and 'completed' in t:
                self.click(f,915,288,'Open completed city quest',1.5)
            elif 'complete' in t and ('reward' in t or 'rewards' in t):
                self.click_word(f,'complete',box=(250,150,900,610), exact=True)
            elif 'town hall' in t and ('upgrade' in t or 'level' in t):
                self.click_word(f,'ok',box=(200,200,920,600),exact=True)
            elif 'barracks' in t and ('recruit' in t or 'troops' in t):
                if self.barracks_visible(f):self.finish_city();return
            elif 'recruitment cost' in t or ('amount' in t and 'lancer' in t):
                if self.screen_state(f)=='recruit_form':self.finish_city();return
            elif 'campaign' in t and ('click' in t or 'button' in t):
                self.click(f,1027,533,'Open Campaign')
            elif 'enter' in t and ('campaigning' in t or 'worg lair' in t):
                self.click(f,464,357,'Enter Worg Lair')
            elif 'recruit' in t and 'completed' in t:
                self.click(f,900,292,'Complete recruitment quest')
            elif 'click' in t and 'barracks' in t:
                self.click(f,555,413,'Open Barracks')
            self.stop.wait(.1)
        raise TimeoutError('Tutorial exceeded six minutes; stopped for inspection rather than blind replay.')

    def finish_city(self):
        f=self.snapshot()
        form_open='recruitment cost' in self.read(f,(480,285,690,313),psm=7)
        if not self.barracks_visible(f) and not form_open:
            f=self.snapshot()
            self.click(f,555,413,'Open Barracks')
            f=self.wait('Waiting for Barracks',lambda f:f if self.barracks_visible(f) else None,8)
        complete='pleted' in self.read(f,(914,283,980,307),psm=11,scale=3)
        if complete:self.mark_checkpoint(recruit_done=True)
        if not self.checkpoint.get('recruit_done'):
            if self.checkpoint.get('recruit_submitted'):
                f=self.wait('Rechecking recruitment already submitted',lambda f:f if 'pleted' in self.read(f,(914,283,980,307),psm=11,scale=3) else None,15)
                self.mark_checkpoint(recruit_done=True)
            else:
                if not form_open:
                    f=self.snapshot();self.click(f,794,317,'Recruit Lancers')
                f=self.wait_text('Waiting for recruitment form',(478,280,690,386),r'recruitment cost|amount',15)
                self.desktop.fill(f,614,372,'200')
                f=self.snapshot()
                if '200' not in self.read(f,(578,358,654,385),psm=7,scale=3):
                    raise TimeoutError('Could not verify recruitment quantity 200.')
                self.mark_checkpoint(recruit_submitted=True)
                f=self.snapshot();self.click(f,449,435,'Recruit 200 Lancers')
                f=self.wait('Verifying recruitment',lambda f:f if 'pleted' in self.read(f,(914,283,980,307),psm=11,scale=3) else None,15)
                self.mark_checkpoint(recruit_done=True)
        f=self.snapshot()
        if 'recruitment cost' not in self.read(f,(480,285,690,313),psm=7):
            f=self.snapshot();self.click(f,794,317,'Dismiss remaining recruit guide')
            f=self.wait_text('Waiting for recruitment form',(478,280,690,386),r'recruitment cost|amount',15)
        f=self.snapshot()
        self.desktop.key(f,keyboard.Key.esc);self.stop.wait(.2)
        f=self.snapshot();self.click(f,896,156,'Close Barracks')
        def campaign(f):
            if self.vision.locate(f,'dialogue',(595,630,830,670),.78):
                self.click(f,690,654,'Continue dialogue',.45);return None
            return f if self.vision.locate(f,'campaign_prompt',(500,470,930,625),.88) else None
        f=self.wait('Waiting for Campaign tutorial',campaign,30)
        self.enter_campaign(f)

    def enter_campaign(self,f,map_open=False):
        if not map_open:self.click(f,1027,533,'Open Campaign')
        f=self.wait_text('Waiting for Worg Lair',(250,240,525,375),r'worg lair|normal|heroic',20)
        self.click(f,464,357,'Enter Worg Lair')
        f=self.wait('Waiting for Worg Lair staging area',lambda f:f if self.vision.locate(f,'worg_challenge',(310,470,490,590),.9) else None,30)
        f.image.save(self.run_dir/'level11.png')

    def click_word(self,f,word,box=None,exact=False):
        im=f.image.crop(box) if box else f.image
        data=pytesseract.image_to_data(ImageOps.grayscale(im).resize((im.width*2,im.height*2)),config='--psm 11',output_type=pytesseract.Output.DICT)
        matches=[]
        for i,text in enumerate(data['text']):
            clean=re.sub(r'[^a-z0-9]','',text.lower())
            if clean==word if exact else word in clean:
                matches.append((data['left'][i]/2+data['width'][i]/4+(box[0] if box else 0), data['top'][i]/2+data['height'][i]/4+(box[1] if box else 0)))
        if len(matches)==1:
            self.click(f,*matches[0],f'Click {word}',1)
            return True
        return False

    def character_creation(self,f,username,named):
        if named:
            return  # Do not resubmit while creation is loading.
        if not 4 <= len(username) <= 12:
            raise ValueError('Character names require 4–12 characters.')
        self.click(f,285,477,'Select male Knight')
        self.stop.wait(.2)
        for attempt in range(3):
            f=self.snapshot()
            if not f or not self.vision.locate(f,'create_start',(745,590,930,650),.86):
                raise TimeoutError('Character creation screen changed; rechecking current state.')
            self.desktop.fill(f,890,554,username)
            # Re-observe before retyping: Flash may not have painted the input yet.
            for observation in range(3):
                f=self.snapshot()
                if f and self.vision.locate(f,'create_start',(745,590,930,650),.86):
                    if self.vision.character_name_matches(f,username):
                        self.click(f,833,620,'Create Knight '+username)
                        return
                self.stop.wait(.2)
            self.log(f'Character name not yet verified; entry attempt {attempt+1}/3')
        raise TimeoutError('Character name entry could not be verified after 3 attempts.')

    def rewards(self,code,start='code'):
        if start=='code':
            def hot_events(f):
                if 'hot events' in self.read(f,(490,115,640,144)):
                    return f
                if self.vision.locate(f,'worg_challenge',(310,470,490,590),.9):
                    self.click(f,826,67,'Open Hot Events',2)
                return None
            f=self.wait('Opening Hot Events',hot_events,30)
            self.click(f,279,559,'Event Prizes')
            f=self.wait_text('Waiting for code form',(495,384,731,409),r'enter your code',30)
            self.desktop.fill(f,649,374,code)
            f=self.snapshot()
            text=self.read(f,(495,357,830,389),psm=7)
            if re.sub(r'[^a-z0-9]','',code.lower()) not in re.sub(r'[^a-z0-9]','',text):
                raise TimeoutError('Redeem code entry could not be verified.')
            self.mark_checkpoint(code_submitted=True)
            f=self.snapshot(); self.click(f,877,374,'Redeem code')
            self.stop.wait(1)
            f=self.snapshot(); self.click(f,945,130,'Close Hot Events')
            f=self.snapshot(); self.click(f,928,77,'Open Mail')
        if start in ('code','mail'):
            self.collect_mail()
        if start in ('code','mail','exchange'):
            self.exchange()
        f=self.snapshot()
        if not self.vision.locate(f,'bag_level11',(270,182,326,216),.90):
            self.click(f,583,634,'Open bag')
        f=self.wait_text('Verifying final bag',(275,186,450,215),re.escape(self.account.lower()),20)
        if not self.vision.locate(f,'bag_level11',(270,182,326,216),.90):
            raise RuntimeError('Final character level not verified as 11.')
        return f

    def collect_mail(self):
        f=self.snapshot()
        if self.vision.locate(f,'worg_challenge',(310,470,490,590),.9):
            self.click(f,928,77,'Open Mail')
            f=self.snapshot()
        elif 'hot events' in self.read(f,(490,115,640,144)):
            f=self.snapshot();self.click(f,945,130,'Close Hot Events')
            f=self.snapshot();self.click(f,928,77,'Open Mail')
            f=self.snapshot()
        if not self.vision.locate(f,'mail_sylph15',(635,265,770,295),.88):
            f=self.wait_text('Waiting for reward mail',(442,211,630,232),r'start.{0,5}pack',30)
            self.click(f,530,228,'Read starter-pack mail')
        f=self.wait('Verifying 15 Sylph Calls in mail',lambda f:f if self.vision.locate(f,'mail_sylph15',(635,265,770,295),.88) else None,20)
        if not self.mail_empty(f):self.click(f,785,554,'Take all mail rewards')
        # Attachments must visibly disappear before leaving Mail.
        f=self.wait('Verifying mail collected',lambda f:f if self.mail_empty(f) else None,20)
        self.mark_checkpoint(mail_collected=True)
        self.click(f,493,134,'Close Mail')

    def exchange(self):
        f=self.snapshot()
        if 'lucky exchange' not in self.read(f,(450,44,652,71)):
            f=self.snapshot(); self.click(f,601,67,'Open Lucky Exchange')
        f=self.wait_text('Waiting for Lucky Exchange',(450,44,652,71),r'lucky exchange',30)
        self.click(f,226,173,"Select Sylph's Call")
        if not self.checkpoint.get('exchange_submitted'):
            f=self.wait_text('Verifying 15 exchange items',(485,302,567,330),r'15',20)
            self.desktop.fill(f,880,293,'15')
            f=self.snapshot()
            if '15' not in self.read(f,(850,283,909,304),psm=7,scale=3):
                raise TimeoutError('Could not verify 15 in exchange count.')
            self.mark_checkpoint(mail_collected=True,exchange_submitted=True)
            f=self.snapshot(); self.click(f,853,250,'Exchange 15 Sylph Calls')
        f=self.wait_text('Verifying exchange completed',(488,309,551,329),r'own\s*:?\s*0',20)
        f.image.save(self.run_dir/'exchange.png')
        self.mark_checkpoint(exchange_done=True)
        self.click(f,954,58,'Close Lucky Exchange')

    def run_account(self,account,password,code,resume=False,phase='code',skip_completed=False):
        self.account=account; self.started=time.monotonic(); self.last_action={}
        self.run_dir=RUNS / (datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+account)
        self.run_dir.mkdir(parents=True,exist_ok=True)
        self.log(f'Starting {account}: {self.region}, server {self.server_choice}')
        try:
            if not resume:
                self.login(account,password)
            if self.server is None:raise ValueError('A numbered server is required when resuming an already-open game.')
            progress_path=BASE/'smart_bot_progress.json'
            progress=json.loads(progress_path.read_text()) if progress_path.exists() else {}
            if skip_completed and was_completed(progress,account,self.region,self.server,code):
                self.log('Already completed on the selected region and server; skipping.')
                self.desktop.close()
                return {'status':'skipped','account':account}
            self.checkpoint_path=BASE/'smart_bot_checkpoints.json'
            self.checkpoint_key=progress_key(account,self.region,self.server,code)
            self.load_checkpoint()
            frame=self.execute_workflow(code,phase)
            image_path=self.run_dir/'bag.png'; frame.image.save(image_path)
            self.enter('Sending verified bag to Telegram')
            result=TelegramClient(BASE/'.telegram_credentials').send_image(image_path.read_bytes(),frame.image.size,
                f'{account} | {self.region} | Server {self.server} | Level 11\nCode {code}; mail collected; 15 Sylph exchanges completed.')
            elapsed=round(time.monotonic()-self.started,2)
            outcome={'account':account,'region':self.region,'server':self.server,'code':code,'status':'complete','seconds':elapsed,
                     'full_run':not resume and phase=='code',
                     'telegram_message_id':result.get('message_id'),'screenshot':str(image_path)}
            atomic_json(self.run_dir/'result.json',outcome)
            progress_path=BASE/'smart_bot_progress.json'
            progress=json.loads(progress_path.read_text()) if progress_path.exists() else {}
            progress[progress_key(account,self.region,self.server,code)]=outcome
            atomic_json(progress_path,progress)
            self.log(f'Telegram confirmed; {elapsed:.1f} seconds'+(' (resumed segment)' if resume else ''))
            self.desktop.close()
            self.log('Done; Wartune closed')
            return outcome
        except Exception as exc:
            self.log(f'STOPPED: {exc}')
            if self.frame: self.frame.image.save(self.run_dir/'stopped.png')
            atomic_json(self.run_dir/'result.json',{'account':account,'status':'stopped','stage':self.stage,'error':str(exc)})
            raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--account',required=True)
    parser.add_argument('--code',required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--phase',choices=['code','mail','exchange','bag'],default='code')
    parser.add_argument('--region',choices=list(REGIONS),default='R2-US West')
    parser.add_argument('--server',default='1218',help='Server number or first')
    args=parser.parse_args()
    (BASE/'.smart_bot_stop').unlink(missing_ok=True)
    password=load_password()
    if not password: raise SystemExit('Set the password in Smart Bot first.')
    stop=threading.Event()
    listener=keyboard.Listener(on_press=lambda key: stop.set() if key==keyboard.Key.f9 else None)
    listener.start()
    lock=RecorderInstance(BASE/'smart-bot-session')
    try:
        Bot(stop,lambda line: print(line,flush=True),region=args.region,server=args.server).run_account(args.account,password,args.code,args.resume,args.phase)
    finally:
        listener.stop(); lock.close()


if __name__=='__main__':
    main()
