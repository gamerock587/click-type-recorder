"""Re-observe after timeouts and resume only from recognized, verified game states."""
import json
import time

import cv2
import numpy as np


class SmartRecovery:
    def load_checkpoint(self):
        data=json.loads(self.checkpoint_path.read_text()) if self.checkpoint_path.exists() else {}
        self.checkpoint=data.get(self.checkpoint_key,{})

    def mark_checkpoint(self,**values):
        self.checkpoint.update(values)
        data=json.loads(self.checkpoint_path.read_text()) if self.checkpoint_path.exists() else {}
        data[self.checkpoint_key]=self.checkpoint
        temporary=self.checkpoint_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,indent=2),encoding='utf-8')
        temporary.replace(self.checkpoint_path)

    def barracks_visible(self,f):
        return bool(self.vision.locate(f,'barracks_title',(420,132,690,177),.84) or (
            self.vision.locate(f,'barracks_lancer',(330,282,469,330),.88) and
            self.vision.locate(f,'barracks_recruit',(750,291,842,341),.86)))

    def mail_empty(self,f):
        if not self.vision.locate(f,'mail_sylph15',(635,265,770,295),.88):return False
        hsv=cv2.cvtColor(np.array(f.image.crop((585,468,863,516))),cv2.COLOR_RGB2HSV)
        return bool(np.mean((hsv[:,:,1]>130)&(hsv[:,:,2]>130)) < .06)

    def screen_state(self,f):
        if not f or f.image.size!=(1088,671):return 'unknown'
        def has(name,box,threshold=.87):return self.vision.locate(f,name,box,threshold)
        if has('bag_level11',(270,182,326,216),.9):return 'bag'
        if has('mail_sylph15',(635,265,770,295),.88):return 'mail_detail'
        if self.barracks_visible(f):
            text=self.read(f,(478,280,690,386))
            return 'recruit_form' if 'recruitment cost' in text or 'amount' in text else 'barracks'
        # The recruit popup may cover the title and first row simultaneously.
        if 'recruitment cost' in self.read(f,(480,285,690,313),psm=7):return 'recruit_form'
        for name,box,state in [
            ('campaign_prompt',(500,470,930,625),'campaign_prompt'),
            ('open_barracks',(420,70,810,180),'open_barracks'),
            ('city_complete',(830,260,1010,315),'tutorial'),
            ('tutorial_ok',(300,200,850,570),'tutorial'),
            ('dialogue',(595,630,830,670),'tutorial'),
            ('intro',(450,370,740,480),'tutorial'),
            ('learn_skill',(480,270,620,420),'tutorial'),
            ('use_ultimate',(185,470,620,555),'tutorial'),
            ('use_slasher',(185,470,620,555),'tutorial'),
            ('create_start',(745,590,930,650),'tutorial'),
            ('worg_challenge',(310,470,490,590),'worg'),
        ]:
            if has(name,box,.84):return state
        if 'lucky exchange' in self.read(f,(450,44,652,71)):return 'lucky'
        if 'hot events' in self.read(f,(490,115,640,144)):return 'hot_events'
        if 'mail' in self.read(f,(482,119,570,144),psm=7):return 'mail_list'
        text=self.read(f,(255,260,517,337),psm=11)
        if 'worg lair' in text and ('normal' in text or 'heroic' in text):return 'campaign_map'
        return 'unknown'

    def next_reward_phase(self):
        if self.checkpoint.get('exchange_done'):return 'bag'
        if self.checkpoint.get('mail_collected') or self.checkpoint.get('exchange_submitted'):return 'exchange'
        if self.checkpoint.get('code_submitted'):return 'mail'
        return 'code'

    def recovery_route(self,state):
        if state in ('barracks','recruit_form','open_barracks'):return 'city'
        if state in ('campaign_prompt','campaign_map'):return 'campaign'
        if state=='tutorial':return 'tutorial'
        if state in ('worg','hot_events'):return self.next_reward_phase()
        if state in ('mail_list','mail_detail'):return 'mail'
        if state=='lucky':return 'exchange'
        if state=='bag':
            if self.checkpoint.get('exchange_done'):return 'bag'
            if self.checkpoint.get('exchange_submitted'):return 'verify_exchange'
        return None

    def execute_workflow(self,code,phase='code'):
        """Normal fast path, followed by bounded state reconciliation after missed transitions."""
        try:
            if phase=='code':
                self.tutorial(self.account)
                phase=self.next_reward_phase()
            return self.rewards(code,phase)
        except TimeoutError as exc:
            self.log(f'{exc}. Rechecking the current screen instead of stopping.')
        recovery_started=time.monotonic()
        for attempt in range(1,7):
            self.desktop.check()
            self.log(f'Recovery {attempt}/6: detecting current screen')
            previous=None; state=None
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                f=self.snapshot()
                observed=self.screen_state(f)
                if observed!='unknown' and observed==previous:
                    state=observed;break
                previous=observed
                self.stop.wait(.25)
            if state:
                route=self.recovery_route(state)
                self.log(f'Recovery detected {state}; route: {route or "wait for a verified state"}')
                try:
                    if route in ('city','campaign','tutorial'):
                        if route=='city':self.finish_city()
                        elif route=='campaign':self.enter_campaign(self.snapshot(),map_open=state=='campaign_map')
                        else:self.tutorial(self.account)
                        return self.rewards(code,self.next_reward_phase())
                    if route=='verify_exchange':
                        self.desktop.key(self.snapshot(),self.escape_key)
                        route='exchange'
                    if route in ('code','mail','exchange','bag'):
                        return self.rewards(code,route)
                except TimeoutError as exc:
                    self.log(f'Recovery is still waiting: {exc}')
            if time.monotonic()-recovery_started>180:break
        raise TimeoutError('Recovery could not verify a safe next step. State saved; no unverified completion or repeat exchange.')
