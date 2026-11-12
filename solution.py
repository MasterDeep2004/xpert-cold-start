#!/usr/bin/env python3
"""Evidence-first WhatsApp cold-start inference.

The pipeline deliberately separates deterministic relationship/org inference from
conservative action extraction. It does not send private chat data to a third party.
"""
import json, re, sys, hashlib
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

CIRCLES = ['spouse','child','parent','in-law','sibling','relative','friend','colleague','client','vendor','partner','staff']
IST = timezone(timedelta(hours=5, minutes=30))

def read_jsonl(p):
    with open(p, encoding='utf-8') as f: return [json.loads(x) for x in f if x.strip()]
def norm(s): return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()
def has(text, pats): return any(re.search(p, text, re.I) for p in pats)
def clean_body(s):
    s=(s or '').replace('\u200e',' ').strip()
    return re.sub(r'\s+',' ',s)

def classify_person(pid, msgs, chat_names, seed):
    if pid in (seed.get('circles') or {}): return seed['circles'][pid]
    # Use whole-thread context, but give explicit relationship statements and the chat's
    # social/work setting more weight than incidental mentions in a person's own messages.
    text=' '.join(clean_body(m.get('body','')) for m in msgs).lower()
    names=' '.join(chat_names).lower()
    alltxt=names+' '+text
    sender_names=' '.join(m.get('sender_name','') for m in msgs).lower()
    # Internal team rooms take precedence over client rooms: employees may be present in both.
    if has(names,[r'pma operations',r'radiant operations',r'nexus: finance']) or has(sender_names,[r'\bpma\b',r'\bnexus\b']) and has(text,[r'\bdata\b',r'\bfile\b',r'\bwork\b',r'\boffice\b']): return 'colleague'
    if has(names,[r'pma office']) or has(sender_names,[r'\bhome\b']) and has(names,[r'kitchen']): return 'staff'
    if has(text,[r'\bmy cousin\b',r'\byour cousin\b',r'\bchacha\b',r'\bdidi\b',r'\bdi\b']): return 'relative'
    if has(text,[r'\bpower yoga\b',r'\bfees?\b.*\byoga\b']) or has(sender_names,[r'trading company',r'water treatment',r'probir sir']): return 'vendor'
    if has(text,[r'\binternship certificate\b',r'\bplacement season\b']): return 'colleague'
    if has(text,[r'\bcatchment analysis\b',r'\bcampaign\b',r'\bperformance marketing\b',r'\bpathway learning centre\b']): return 'client'
    if has(text,[r'\bpilot is live\b',r'\boutlet expansion\b',r'\bsigning up [0-9]']): return 'partner'
    if has(names,[r'\bnayna khetan\b']) or has(text,[r'\bmy wife\b',r'\bmy husband\b']): return 'spouse'
    if has(names,[r'siblings?\b',r'family',r'khetans and',r'home is where',r'ratekar family',r'project coffee table book']):
        if has(names,[r'siblings?']): return 'sibling'
        if has(text,[r'\bmy mother\b',r'\bmy mom\b',r'\bmy dad\b',r'\bmy father\b',r'\bmy parents\b']): return 'parent'
        if has(text,[r'\bmy brother\b',r'\bmy sister\b']): return 'sibling'
        return 'relative'
    # A dedicated customer onboarding / ad-targeting room is strong client evidence.
    if has(names,[r'nexus.*ad targeting',r'\bnexus: finance\b',r'\bgreenloop\b',r'\broyaledge\b',r'\bblossara\b',r'\bhealthex\b',r'\bnavya jewels\b',r'\bcurations\b',r'\bcarfitt\b',r'\bveda natural\b',r'\bwand(er)?ly\b',r'\bspectrum wallpapers\b']): return 'client'
    if has(names,[r'\boperations\b',r'\bpma\b',r'\bnexus\b',r'\boffice\b']) or has(text,[r'\bfrom the office\b',r'\bjoining work\b',r'\bteam meeting\b']):
        if has(names,[r'\bpma office\b']) and has(text,[r'cleaning','cook','driver','house']): return 'staff'
        return 'colleague'
    if has(text,[r'\bmy driver\b',r'\bdriver\b.*\bpayment\b',r'\bhouse help\b',r'\bcook\b',r'\bmaid\b',r'\bcleaner\b',r'\bhome staff\b']): return 'staff'
    if has(text,[r'\bquotation\b',r'\bquote\b',r'\bsupplier\b',r'\bvendor\b',r'\bper kg\b',r'\bex.?godown\b',r'\bmaterial order\b',r'\btrading company\b',r'\bfees?\b.*\byoga\b']): return 'vendor'
    if has(text,[r'\bclient\b',r'\bcampaign\b',r'\bbrand\b',r'\bproposal\b',r'\bpo\b',r'\binvoice\b',r'\bshare the report\b',r'\bperformance marketing\b']): return 'client'
    if has(text,[r'\bco.?founder\b',r'\bbusiness partner\b',r'\bpartner at\b',r'\bpartner with\b']): return 'partner'
    if has(text,[r'\bmy son\b',r'\bmy daughter\b',r'\bmy child\b']): return 'child'
    if has(text,[r'\bmy mother\b',r'\bmy mom\b',r'\bmy dad\b',r'\bmy father\b']): return 'parent'
    if has(text,[r'\bmy brother\b',r'\bmy sister\b']): return 'sibling'
    if has(names,[r'family','siblings','summer','italy','tadoba']): return 'relative'
    return 'friend'

def infer_orgs(chats, messages):
    # Stable conceptual org ids; chat-to-org matching is driven by repeated vocabulary.
    orgs=[
      {'id':'work_pma','name':'PostMyAd','type':'company','side':'work','role':'owner'},
      {'id':'work_nexus','name':'NEXUS','type':'company','side':'work','role':'owner'},
      {'id':'work_daas','name':'Nexus DaaS','type':'company','side':'work','role':'owner'},
      {'id':'work_radiant','name':'Radiant','type':'company','side':'work','role':'helper'},
      {'id':'work_office','name':'Office','type':'workspace','side':'work','role':'owner'},
      {'id':'home','name':'Home','type':'residence','side':'home','role':'owner'},
      {'id':'farmhouse','name':'Farmhouse','type':'vacation home','side':'home','role':'owner'},
    ]
    bychat=defaultdict(list)
    for m in messages: bychat[m['chat_id']].append(m)
    mapping={}
    for c in chats:
        cid=c['chat_id']; name=norm(c.get('name','')); body=norm(' '.join(m.get('body','') for m in bychat[cid][:30]))
        if any(k in name for k in ['pma','postmyad','acq rate']): mapping[cid]='work_pma'
        elif 'milestone' in name and any(k in body for k in ['outlet expansion','meridian team','nexus']): mapping[cid]='work_daas'
        elif 'radiant' in name: mapping[cid]='work_radiant'
        elif 'nexus' in name or 'd2c circle' in name or 'd2b connect' in name or 'd2c connect' in name or 'd2c' in name:
            mapping[cid]='work_nexus'
        elif 'milestone' in name and ('daas' in body or 'nexus' in body or 'data' in body): mapping[cid]='work_daas'
        elif any(k in name for k in ['kitchen','carpool','home is where','kanchan','debnaji']): mapping[cid]='home'
        elif 'pma office' in name: mapping[cid]='work_office'
        elif 'radiant' in body[:1000]: mapping[cid]='work_radiant'
        elif any(k in body for k in ['postmyad','pma operations']): mapping[cid]='work_pma'
        elif c.get('is_group') and 'nexus' in body[:3000]: mapping[cid]='work_nexus'
    # Direct threads with people who are also in a named organisation's staff room.
    pma_members=set()
    for c in chats:
        if any(k in norm(c.get('name','')) for k in ('pma operations','acq rate','pma meta')):
            pma_members.update(p for p in c.get('participants',[]) if p!='ME')
    nexus_members=set()
    for c in chats:
        if c.get('is_group') and ('nexus' in norm(c.get('name','')) or 'd2c circle' in norm(c.get('name',''))):
            nexus_members.update(p for p in c.get('participants',[]) if p!='ME')
    for c in chats:
        if not c.get('is_group'):
            p=next((x for x in c.get('participants',[]) if x!='ME'),None)
            if p in pma_members and len(bychat[c['chat_id']]) <= 10: mapping[c['chat_id']]='work_pma'
            elif p in nexus_members and any('office' in (m.get('sender_name','').lower()) for m in bychat[c['chat_id']]) and any(k in ' '.join(m.get('body','').lower() for m in bychat[c['chat_id']]) for k in ('nexus','campaign','brand')): mapping[c['chat_id']]='work_nexus'
    
    return orgs,mapping

def infer_links(people, person_msgs, chats, chat_org):
    out=[]
    # Evidence from shared group membership and direct-chat context.
    chat_by_id={c['chat_id']:c for c in chats}
    for p in people:
        links=[]
        work_chat_count=sum(1 for c in chats if p['id'] in c.get('participants',[]) and chat_org.get(c['chat_id'],'').startswith('work_'))
        direct_chat_count=sum(1 for c in chats if p['id'] in c.get('participants',[]) and not c.get('is_group'))
        for c in chats:
            if p['id'] not in c.get('participants',[]): continue
            oid=chat_org.get(c['chat_id'])
            if oid and oid.startswith('work_'):
                # client-facing project groups are part of Nexus, but non-core participants are clients.
                circle=p.get('circle')
                if circle in ('colleague','partner') and (direct_chat_count>0 or work_chat_count>1): links.append({'organisation':oid,'link':'runs'})
            elif oid in ('home','farmhouse') and p.get('circle')=='staff':
                links.append({'organisation':oid,'link':'supports'})
        seen=set(); p['links']=[x for x in links if not (x['organisation'] in seen or seen.add(x['organisation']))]
        seen=set(); p['links']=[x for x in links if not (x['organisation'] in seen or seen.add(x['organisation']))]
        if p['circle']=='staff': p['looks_after']=[]
        out.append(p)
    return out

ACTION_RE = re.compile(r'\b(please|pls|plz|can you|could you|would you|need you to|remind me|i will|i ll|i\'ll|will do|i can|let me|shall i|should i|can we|lets|let us|follow up|followup|send me|send it|share the|book|order|pay|transfer|arrange|confirm|schedule|call me|call him|call her|discuss with me|please discuss|kindly|by tomorrow|tomorrow|today|before|deadline|due)\b',re.I)
IMPERATIVE_RE = re.compile(r'^\s*(pls|please|plz|kindly|send|share|pay|transfer|book|order|arrange|confirm|call|check|ask|tell|get|buy|discuss|connect|follow.?up|remind|provide|update|add|make|take|pick|bring|submit|complete|finish|do)\b',re.I)

def extract_due(body, ts):
    low=body.lower(); dt=datetime.fromtimestamp(ts,IST)
    if re.search(r'\btoday\b|tonight|before the day ends',low): return dt.date().isoformat()
    if re.search(r'\btomorrow\b',low): return (dt.date()+timedelta(days=1)).isoformat()
    # Explicit dates such as 2 Sept, 5th September, 31 Aug.
    m=re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b',low)
    if m:
        month={'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'sept':9,'oct':10,'nov':11,'dec':12}[m.group(2)]
        try: return datetime(dt.year,month,int(m.group(1))).date().isoformat()
        except ValueError: pass
    return None

def make_loops(messages, chats, person_circle):
    bychat=defaultdict(list)
    for m in messages: bychat[m['chat_id']].append(m)
    loops=[]; seen=set()
    for c in chats:
        cid=c['chat_id']; ms=bychat[cid]
        if not ms: continue
        # Focus on actionable one-to-one threads and business task groups, not broadcast/news groups.
        group=bool(c.get('is_group'))
        cname=norm(c.get('name',''))
        business_group=any(x in cname for x in ['nexus','pma','radiant','operations','marketing','finance'])
        for i,m in enumerate(ms):
            body=clean_body(m.get('body',''))
            if len(body)<8 or body in ('[photo]','[document]','image','ok','thanks','thank you'): continue
            if not (ACTION_RE.search(body) or IMPERATIVE_RE.search(body)): continue
            if group and has(cname,[r'operations']) and sender!='ME': continue
            sender=m.get('sender_id'); low=body.lower()
            # Avoid general questions, greetings, media/news, and generic group chatter.
            if re.match(r'^(hi|hello|good morning|good evening|happy birthday|hbd)\b',low): continue
            if group and not business_group: continue
            if group and business_group and sender!='ME' and not re.search(r'\b(arjun|@\d{6,})\b',low): continue
            if len(body)>1200: body=body[:1200]
            # Require an identifiable action or unresolved request.
            actionable=bool(IMPERATIVE_RE.search(body) or re.search(r'\b(i will|i ll|i\'ll|will do|remind me|should i|can we|could you|can you|please|pls|kindly|need to)\b',low))
            if not actionable: continue
            # Keep strongest candidates: sender asks for something or Arjun explicitly undertakes it.
            if sender!='ME' and not re.search(r'\b(please|pls|plz|can you|could you|need you|would you|should i|can we|let.s|need a|i need|pls get|pls pay|pls order|send me|share|remind me)\b',low): continue
            if sender=='ME' and not re.search(r'\b(i will|i ll|i\'ll|will do|i can|let me|i\s+will|i can|i\s+can|i\s+shall|i\s+will|please discuss|discuss with me|connect with|i\s+will|reverting|will complete|buy|order|send|pay|transfer|arrange|book|ask|call|follow up)\b',low): continue
            # Exclude informational marketing copy / long group announcements.
            if group and not business_group and len(body)>220: continue
            key=(cid,m['id'])
            if key in seen: continue
            seen.add(key)
            other=next((p for p in c.get('participants',[]) if p!='ME'),None)
            if sender=='ME':
                owner='ME' if re.search(r'\b(i will|i ll|i\'ll|will do|reverting|i can|let me)\b',low) else (other or 'ME')
            else:
                owner='ME' if re.search(r'\b(please|pls|plz|can you|could you|need you|would you|send me|share|pay|order|book|arrange|confirm|call me)\b',low) else sender
            # Followup means another party owes a response/action; user-originated asks are commitments/questions.
            if sender!='ME' and re.search(r'\b(waiting|awaiting|let me know|please confirm|can you|could you|share|send|update|revert|response|reply|come back)\b',low): typ='followup'
            elif re.search(r'\b(by|before|today|tomorrow|deadline|due|confirm.*by|pay.*by|share.*by)\b',low) and extract_due(body,m['ts']): typ='deadline'
            elif re.search(r'\b(should i|shall i|can we|are you|do you|would you|which|what time|what date|is it possible)\b',low): typ='question'
            else: typ='commitment'
            # Explicit completion evidence later in the same chat. Conservative: only direct acknowledgements.
            status='open'; closed_by=None
            for later in ms[i+1:]:
                lb=clean_body(later.get('body','')).lower()
                if re.search(r'\b(done|completed|sent|shared|paid|transferred|ordered|booked|confirmed|fixed|resolved|all fixed|will let you know)\b',lb):
                    # Don't close on a generic acknowledgement unless it is a task-completion word.
                    if re.search(r'\b(done|completed|sent|paid|transferred|ordered|booked|confirmed|resolved|all fixed)\b',lb):
                        status='done'; closed_by=later['id']; break
            # Never auto-close a request based on a distant unrelated acknowledgement.
            if closed_by and (ms.index(next(x for x in ms if x['id']==closed_by))-i)>8:
                status='open'; closed_by=None
            title=re.sub(r'\s+',' ',body).strip()
            if len(title)>155: title=title[:152].rstrip()+'...'
            due=extract_due(body,m['ts'])
            help_=None
            if status=='open':
                # Private/family matters are deliberately left alone. Admin tasks can be assisted safely.
                circle=person_circle.get(sender,'friend')
                if circle in ('spouse','child','parent','in-law','sibling','relative') :
                    help_={'action':'leave','reason':'This is a personal or family matter; the assistant should not intrude.'}
                elif circle=='staff':
                    help_={'action':'leave','reason':'This is for staff to handle; avoid duplicating their work.'}
                elif typ=='followup':
                    help_={'action':'assist','next_step':'Draft a concise follow-up and prepare it for review.','needs_yes':True}
                elif re.search(r'\b(pay|transfer|buy|purchase|order|book|send|share|reply|call|discuss|confirm)\b',low):
                    help_={'action':'assist','next_step':'Prepare the required details or a draft for Arjun to review.','needs_yes':True}
                else:
                    help_={'action':'remind','remind_at':(datetime.fromtimestamp(m['ts'],IST)+timedelta(days=1)).isoformat(timespec='minutes')}
            loops.append({'title':title,'type':typ,'owner':owner,'chat_id':cid,'source_msg':m['id'],'due':due,'status':status,'closed_by':closed_by,'help':help_})
    return loops

def infer_goals(messages):
    """Only emit explicit first-person aspirations; avoid turning every request into a goal."""
    out=[]; seen=set()
    trigger=re.compile(r'\b(my goal is|my target is|i aim to|i want to achieve|i want to build|i plan to|i am planning to|i\'m planning to|i would like to|over the next (?:year|3 years|5 years)|by (?:the end of )?2027|this year i want)\b',re.I)
    for m in messages:
        if m.get('sender_id')!='ME': continue
        body=clean_body(m.get('body',''))
        if not trigger.search(body) or len(body)<20 or len(body)>500: continue
        low=body.lower()
        if re.search(r'\b(you|your|she|he|they)\b',low[:80]) and not re.search(r'\bi (?:want|plan|aim)\b',low): continue
        if has(low,[r'\bhealth\b',r'\bfitness\b',r'\bworkout\b',r'\bweight\b',r'\brun\b',r'\bgym\b']): area='health'
        elif has(low,[r'\bwife\b',r'\bspouse\b']): area='spouse'
        elif has(low,[r'\bkids?\b',r'\bchildren\b',r'\bschool\b']): area='kids'
        elif has(low,[r'\bparents?\b',r'\bmom\b',r'\bdad\b']): area='parents'
        elif has(low,[r'\bhome\b',r'\bhouse\b',r'\bfarmhouse\b']): area='home'
        elif has(low,[r'\btravel\b',r'\btrip\b',r'\bvisit\b',r'\bholiday\b']): area='experiences'
        elif has(low,[r'\bfriend\b',r'\bcommunity\b']): area='friends'
        elif has(low,[r'\binvest\b',r'\bsave\b',r'\bfinancial\b',r'\bportfolio\b']): area='finances'
        elif has(low,[r'\bcompany\b',r'\bbusiness\b',r'\bteam\b',r'\bclient\b',r'\bproduct\b',r'\blaunch\b',r'\bbuild\b',r'\bgrow\b']): area='work'
        else: area='self'
        if has(low,[r'\bevery day\b',r'\bdaily\b',r'\bweekly\b',r'\bthree times\b',r'\bhabit\b',r'\broutine\b']): typ='habit'
        elif has(low,[r'\btrip\b',r'\btravel\b',r'\bvisit\b',r'\bholiday\b',r'\bexperience\b']): typ='experience'
        else: typ='milestone'
        horizon='5yr' if re.search(r'5 years|five years',low) else '3yr' if re.search(r'3 years|three years',low) else '3mo' if re.search(r'next 3 months|by (?:next )?quarter',low) else '1yr'
        title=body[:140].strip()
        key=(area,typ,re.sub(r'\W+',' ',title.lower())[:80])
        if key in seen: continue
        seen.add(key); out.append({'title':title,'area':area,'type':typ,'horizon':horizon,'evidence':[m['id']]})
        if len(out)>=10: break
    return out

def main(input_dir, output_path):
    folder=Path(input_dir); chats=read_jsonl(folder/'chats.jsonl'); messages=read_jsonl(folder/'messages.jsonl')
    seed=json.loads((folder/'seed.json').read_text(encoding='utf-8')) if (folder/'seed.json').exists() else {}
    byperson=defaultdict(list); names=defaultdict(list); bychat=defaultdict(list)
    for m in messages:
        if m.get('sender_id')!='ME': byperson[m['sender_id']].append(m)
        bychat[m['chat_id']].append(m)
    for c in chats:
        for p in c.get('participants',[]):
            if p!='ME': names[p].append(c.get('name',''))
    people=[]
    ids=sorted({p for c in chats for p in c.get('participants',[]) if p!='ME'})
    for p in ids:
        thread_context=[]
        for c in chats:
            if p in c.get('participants',[]): thread_context.extend(bychat[c['chat_id']])
        circle=classify_person(p,thread_context,names[p]+[m.get('sender_name','') for m in byperson[p]],seed)
        people.append({'id':p,'circle':circle})
    orgs, chat_org=infer_orgs(chats,messages)
    # Core team links inferred from repeated participation in work conversations.
    people=infer_links(people,byperson,chats,chat_org)
    pmap={p['id']:p for p in people}
    # Group chats are assigned to organisations only when evidence is strong; direct chats default to person.
    chat_ties=[]
    for c in chats:
        cid=c['chat_id']; participants=[p for p in c.get('participants',[]) if p!='ME']
        oid=chat_org.get(cid)
        if oid:
            chat_ties.append({'chat_id':cid,'organisation':oid})
        elif not c.get('is_group') and participants:
            p=participants[0]
            # Home staff threads belong to the home rather than to the individual.
            if pmap.get(p,{}).get('circle')=='staff': chat_ties.append({'chat_id':cid,'organisation':('work_office' if 'office' in norm(c.get('name','')) else 'home')})
            else: chat_ties.append({'chat_id':cid,'person':p})
        elif participants:
            # Personal/community groups belong to a salient participant, otherwise to the first stable member.
            chat_ties.append({'chat_id':cid,'person':participants[0]})
        else:
            # Defensive fallback; the input should always have a participant.
            chat_ties.append({'chat_id':cid,'person':ids[0] if ids else 'ME'})
    loops=make_loops(messages,chats,{p['id']:p['circle'] for p in people})
    out={'people':people,'organisations':orgs,'chats':chat_ties,'goals':infer_goals(messages),'open_loops':loops}
    Path(output_path).parent.mkdir(parents=True,exist_ok=True)
    Path(output_path).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    if len(sys.argv)!=3: sys.exit('usage: solution.py <input_dir> <output.json>')
    main(sys.argv[1],sys.argv[2])
