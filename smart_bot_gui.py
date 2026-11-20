"""Account range, redeem code, live progress, and emergency stop for Smart Bot."""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pynput import keyboard
from smart_bot import BASE, Bot, REGIONS, was_completed, select_accounts, load_password, save_password, atomic_json, RecorderInstance


class App:
    def __init__(self):
        self.root=tk.Tk(); self.root.title('Wartune Smart Bot v2'); self.root.geometry('710x735+10+70')
        self.events=queue.Queue(); self.stop=threading.Event(); self.worker=None; self.lock=None
        path=BASE/'smart_bot_settings.json'
        saved=json.loads(path.read_text()) if path.exists() else {}
        self.prefix=tk.StringVar(value=saved.get('prefix','demo'))
        self.start=tk.StringVar(value=str(saved.get('start',99)))
        self.end=tk.StringVar(value=str(saved.get('end',99)))
        self.code=tk.StringVar(value=saved.get('code','SQ-SH4DX1'))
        self.password=tk.StringVar(value=load_password())
        self.region=tk.StringVar(value=saved.get('region','R2-US West'))
        self.server_mode=tk.StringVar(value=saved.get('server_mode','First server (top left)'))
        self.server=tk.StringVar(value=str(saved.get('server',1218)))
        self.remember=tk.BooleanVar(value=True)
        self.skip=tk.BooleanVar(value=True)
        self.listed=tk.BooleanVar(value=saved.get('listed',True))
        self.status=tk.StringVar(value='Ready. F9 stops immediately.')
        self.timer=tk.StringVar(value='');self.account_started=None
        form=ttk.Frame(self.root,padding=16); form.pack(fill='x')
        ttk.Label(form,text='Wartune Smart Bot',font=('Segoe UI',18,'bold')).grid(row=0,column=0,columnspan=4,sticky='w',pady=(0,14))
        fields=[('Account prefix',self.prefix),('Start number',self.start),('End number',self.end),('Redeem code',self.code),('Password',self.password)]
        self.inputs=[]
        for row,(label,var) in enumerate(fields,1):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=5)
            entry=ttk.Entry(form,textvariable=var,width=38,show='*' if label=='Password' else '')
            entry.grid(row=row,column=1,columnspan=3,sticky='ew',padx=12);self.inputs.append(entry)
        ttk.Label(form,text='Region').grid(row=6,column=0,sticky='w',pady=5)
        self.region_box=ttk.Combobox(form,textvariable=self.region,values=list(REGIONS),state='readonly',width=35)
        self.region_box.grid(row=6,column=1,columnspan=3,sticky='ew',padx=12)
        ttk.Label(form,text='Server').grid(row=7,column=0,sticky='w',pady=5)
        self.mode_box=ttk.Combobox(form,textvariable=self.server_mode,values=['First server (top left)','Server number'],state='readonly',width=23)
        self.mode_box.grid(row=7,column=1,sticky='ew',padx=12)
        self.server_entry=ttk.Entry(form,textvariable=self.server,width=8);self.server_entry.grid(row=7,column=2,sticky='w')
        self.mode_box.bind('<<ComboboxSelected>>',lambda event:self.update_server_mode())
        self.update_server_mode()
        ttk.Label(form,text='First server follows the new weekly server in the selected region.').grid(row=8,column=0,columnspan=4,sticky='w',pady=(3,7))
        ttk.Label(form,text='Example: 99 → 1 runs downward; 1 → 100 runs upward.').grid(row=9,column=0,columnspan=4,sticky='w',pady=4)
        ttk.Checkbutton(form,text='Remember password (protected by Windows)',variable=self.remember).grid(row=10,column=0,columnspan=4,sticky='w')
        ttk.Checkbutton(form,text='Skip accounts already completed with this code',variable=self.skip).grid(row=11,column=0,columnspan=4,sticky='w')
        ttk.Checkbutton(form,text='Use only accounts listed in accounts.txt',variable=self.listed).grid(row=12,column=0,columnspan=4,sticky='w')
        self.start_button=ttk.Button(form,text='Start bot',command=self.start_run);self.start_button.grid(row=13,column=0,pady=14,sticky='ew')
        ttk.Button(form,text='Stop · F9',command=self.stop_run).grid(row=13,column=1,padx=12,sticky='ew')
        ttk.Button(form,text='Open results',command=self.results).grid(row=13,column=2,sticky='ew')
        ttk.Label(self.root,textvariable=self.status,wraplength=670).pack(fill='x',padx=16)
        ttk.Label(self.root,textvariable=self.timer).pack(fill='x',padx=16)
        self.log=tk.Text(self.root,height=13,wrap='word',state='disabled');self.log.pack(fill='both',expand=True,padx=16,pady=12)
        self.listener=keyboard.Listener(on_press=lambda key:self.stop.set() if key==keyboard.Key.f9 else None);self.listener.start()
        self.root.after(100,self.poll);self.root.protocol('WM_DELETE_WINDOW',self.close)

    def results(self):
        import os
        p=BASE/'smart_bot_runs';p.mkdir(exist_ok=True);os.startfile(p)

    def update_server_mode(self):
        self.server_entry.configure(state='normal' if self.server_mode.get()=='Server number' else 'disabled')

    def start_run(self):
        if self.worker and self.worker.is_alive():return
        try:
            start,end=int(self.start.get()),int(self.end.get())
            listed=None
            if self.listed.get():
                listed=(BASE/'accounts.txt').read_text(encoding='utf-8-sig').splitlines()
                listed=[a.strip() for a in listed if a.strip()]
            accounts,missing=select_accounts(self.prefix.get().strip(),start,end,listed)
            if not accounts:raise ValueError('No accounts selected. Update accounts.txt or turn off the listed-accounts option.')
            if missing:self.events.put('Not in accounts.txt; excluded: '+', '.join(missing))
            code=self.code.get().strip();password=self.password.get()
            region=self.region.get()
            server='first' if self.server_mode.get()=='First server (top left)' else int(self.server.get())
            if region not in REGIONS or (server!='first' and server<1):raise ValueError('Choose a valid region and server.')
            if not code or not password:raise ValueError('Enter the redeem code and password.')
            if not (BASE/'.telegram_credentials').exists():raise ValueError('Configure Telegram in the existing Recorder first.')
            settings={'prefix':self.prefix.get().strip(),'start':start,'end':end,'code':code,'listed':self.listed.get(),
                      'region':region,'server_mode':self.server_mode.get(),'server':self.server.get()}
            atomic_json(BASE/'smart_bot_settings.json',settings)
            if self.remember.get():save_password(password)
            else:(BASE/'.smart_bot_password').unlink(missing_ok=True)
            self.lock=RecorderInstance(BASE/'smart-bot-session')
            self.stop.clear();(BASE/'.smart_bot_stop').unlink(missing_ok=True)
            skip=self.skip.get()
            self.start_button.configure(state='disabled')
            for entry in self.inputs:entry.configure(state='disabled')
            for entry in (self.region_box,self.mode_box,self.server_entry):entry.configure(state='disabled')
            self.worker=threading.Thread(target=self.run,args=(accounts,password,code,skip,region,server),daemon=True);self.worker.start()
        except Exception as exc:messagebox.showerror('Cannot start',str(exc))

    def run(self,accounts,password,code,skip,region='R2-US West',server=1218):
        try:
            bot=Bot(self.stop,self.events.put,region=region,server=server)
            p=BASE/'smart_bot_progress.json'
            completed=json.loads(p.read_text()) if p.exists() else {}
            for index,account in enumerate(accounts,1):
                if self.stop.is_set():break
                if skip and server!='first' and was_completed(completed,account,region,server,code):
                    self.events.put(f'Skipped {account}: already verified and delivered for this code.');continue
                self.events.put(f'Account {index}/{len(accounts)}: {account}')
                self.account_started=time.monotonic()
                bot.run_account(account,password,code,skip_completed=skip)
                # Resolve "first" once per batch, so a new release cannot split the batch.
                if server=='first':
                    server=bot.server;bot.server_choice=server
                    self.events.put(f'Using {region}, server {server} for the rest of this batch.')
            self.events.put('Stopped.' if self.stop.is_set() else 'Selected account range finished.')
        except Exception as exc:self.events.put(f'Stopped for inspection: {exc}')
        finally:
            if self.lock:self.lock.close();self.lock=None
            self.account_started=None
            self.events.put(None)

    def stop_run(self):
        self.stop.set();self.status.set('Stopping safely…')

    def poll(self):
        if self.account_started:
            seconds=int(time.monotonic()-self.account_started)
            self.timer.set(f'Current account: {seconds//60}:{seconds%60:02d} elapsed · Target: under 6:00')
        while not self.events.empty():
            line=self.events.get()
            if line is None:
                self.start_button.configure(state='normal')
                for entry in self.inputs:entry.configure(state='normal')
                self.region_box.configure(state='readonly');self.mode_box.configure(state='readonly');self.update_server_mode()
            else:
                self.status.set(line);self.log.configure(state='normal');self.log.insert('end',line+'\n');self.log.see('end');self.log.configure(state='disabled')
        self.root.after(100,self.poll)

    def close(self):
        self.stop.set()
        if self.worker and self.worker.is_alive():
            self.status.set('Stopping the bot before closing…');self.root.after(200,self.close);return
        self.listener.stop();self.root.destroy()


if __name__=='__main__':App().root.mainloop()
