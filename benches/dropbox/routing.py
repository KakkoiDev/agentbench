"""Requests labelled with who should answer them: 'chat' (the fast model) or 'tools' (the tools model:
files, the web, anything current). The mini cases, plus harder ones without obvious keywords, and
some where a keyword misleads ("write a birthday message" needs no file). English, Japanese, French."""
from cases import MINI, MINI_LANG

TOOLS = {'write-file', 'write-file-list', 'write-file-dates', 'ja-save'}
ROUTING = [dict(id=c['id'], text=c['text'], ambient=c.get('ambient', False), want='tools' if c['id'] in TOOLS else 'chat', set='mini')
           for c in MINI + MINI_LANG]

_HARD = [
    # needs files or the web, but says so without a keyword
    ('tools', 'en', 'can you tidy up my notes from last week?'),
    ('tools', 'en', 'what did I write about the garden project?'),
    ('tools', 'en', 'put together a packing list for Kyoto and keep it with my other travel stuff'),
    ('tools', 'en', 'is the pharmacy near the station open on Sunday?'),
    ('tools', 'en', 'how much is a Shinkansen ticket from Tokyo to Osaka these days?'),
    ('tools', 'en', 'who won the match last night?'),
    ('tools', 'en', "make a copy of the budget with this month's numbers"),
    ('tools', 'en', "what's in the contract I uploaded yesterday?"),
    ('tools', 'en', 'rename the photos from the trip by date'),
    ('tools', 'en', 'find the curry recipe I kept'),
    ('tools', 'ja', '先週のメモを整理してくれる？'),
    ('tools', 'ja', '京都旅行の持ち物リストを作って、旅行のところに置いておいて'),
    ('tools', 'ja', '日曜日に駅前の薬局は開いてる？'),
    ('tools', 'ja', '昨日アップした契約書には何が書いてある？'),
    ('tools', 'fr', 'tu peux ranger mes notes de la semaine dernière ?'),
    ('tools', 'fr', 'la pharmacie près de la gare est ouverte dimanche ?'),
    ('tools', 'fr', "qu'est-ce qu'il y a dans le contrat que j'ai envoyé hier ?"),
    # chat, some with a word that sounds like a tool job
    ('chat', 'en', "what's a good name for a cat?"),
    ('chat', 'en', 'can you write a short birthday message for grandma?'),
    ('chat', 'en', "how do I say 'see you tomorrow' in Japanese?"),
    ('chat', 'en', 'explain why the sky is blue to a 6 year old'),
    ('chat', 'en', 'what should I cook with eggs, rice and leeks?'),
    ('chat', 'en', 'rewrite this more politely: send me the thing now'),
    ('chat', 'en', 'any tips for running a first 5k?'),
    ('chat', 'ja', 'おばあちゃんへの誕生日メッセージを書いて'),
    ('chat', 'ja', '猫にいい名前ある？'),
    ('chat', 'ja', 'これをもっと丁寧に言い換えて：早く送って'),
    ('chat', 'fr', "écris un petit message d'anniversaire pour mamie"),
    ('chat', 'fr', 'comment on dit « à demain » en japonais ?'),
    ('chat', 'fr', 'que cuisiner avec des œufs, du riz et des poireaux ?'),
    ('chat', 'fr', "explique pourquoi le ciel est bleu à un enfant de 6 ans"),
]
ROUTING += [dict(id=f'hard-{lang}-{i}', text='@mini ' + text, ambient=False, want=want, set='hard')
            for i, (want, lang, text) in enumerate(_HARD)]
