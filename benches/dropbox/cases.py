"""DropBox's real jobs, as test cases (see dropbox_bench.py).

SENTENCES: chat messages with English, Japanese and French versions written together, so any
one is the reference translation of the others (6 directions). The same sentences are spoken
by macOS voices for the caption test, so every caption has an exact reference text.

MINI: requests like the ones DropBox's house agent (mini) gets, each with a check that can be
graded without a person: stay silent or answer, facts the answer must (or must not) contain,
a file it must write.
"""

SENTENCES = [
    dict(en="Can we move the meeting to three o'clock tomorrow?",
         ja="明日の会議を三時に変更できますか？",
         fr="Est-ce qu'on peut déplacer la réunion à quinze heures demain ?"),
    dict(en="I just sent you the contract, please check the second page.",
         ja="今契約書を送ったので、二ページ目を確認してください。",
         fr="Je viens de t'envoyer le contrat, vérifie la deuxième page s'il te plaît."),
    dict(en="The train is delayed, I will be about twenty minutes late.",
         ja="電車が遅れているので、二十分ほど遅れます。",
         fr="Le train a du retard, j'arriverai avec environ vingt minutes de retard."),
    dict(en="Thanks a lot, that helps me a lot.",
         ja="本当にありがとう、とても助かります。",
         fr="Merci beaucoup, ça m'aide vraiment."),
    dict(en="Did you remember to buy milk and eggs on the way home?",
         ja="帰りに牛乳と卵を買うのを覚えていましたか？",
         fr="Tu as pensé à acheter du lait et des œufs en rentrant ?"),
    dict(en="The new version of the app crashes when I open the camera.",
         ja="アプリの新しいバージョンは、カメラを開くとクラッシュします。",
         fr="La nouvelle version de l'application plante quand j'ouvre l'appareil photo."),
    dict(en="Let's have dinner at the Italian restaurant near the station on Friday.",
         ja="金曜日に駅の近くのイタリアンレストランで夕食を食べましょう。",
         fr="Allons dîner au restaurant italien près de la gare vendredi."),
    dict(en="My flight to Paris leaves at eight in the morning.",
         ja="パリ行きの私の便は朝八時に出発します。",
         fr="Mon vol pour Paris part à huit heures du matin."),
    dict(en="Could you share the slides from yesterday's presentation?",
         ja="昨日のプレゼンのスライドを共有してもらえますか？",
         fr="Tu pourrais partager les diapositives de la présentation d'hier ?"),
    dict(en="It is raining a lot today, so take an umbrella.",
         ja="今日は雨がたくさん降っているので、傘を持って行ってください。",
         fr="Il pleut beaucoup aujourd'hui, alors prends un parapluie."),
    dict(en="The server was down for an hour last night, but everything works now.",
         ja="昨夜サーバーが一時間止まっていましたが、今はすべて動いています。",
         fr="Le serveur était en panne pendant une heure hier soir, mais tout fonctionne maintenant."),
    dict(en="I don't think we need a bigger budget for this project.",
         ja="このプロジェクトにもっと大きな予算は必要ないと思います。",
         fr="Je ne pense pas qu'on ait besoin d'un budget plus important pour ce projet."),
]

LANG_NAMES = {'en': 'English', 'ja': 'Japanese', 'fr': 'French'}
# Two macOS voices per language: captions must not depend on one voice.
VOICES = {'en': ['Samantha', 'Daniel'], 'ja': ['Kyoko', 'Eddy (Japanese (Japan))'], 'fr': ['Thomas', 'Flo (French (France))']}

_TEAM = [
    ('Ken', 'Morning! Reminder: the design review is Thursday at 2pm in room B.'),
    ('Aiko', 'I will bring the printed mockups.'),
    ('Ken', 'Marc, can you prepare the budget sheet before then?'),
    ('Marc', 'Sure, I will have it ready by Wednesday evening.'),
    ('Aiko', 'Also the client asked to move the launch from May 3 to May 17.'),
    ('Ken', 'OK, then the marketing emails need to go out on May 10 instead.'),
]

# Each check: silent (True = must stay silent, False = must answer), has (all regexes must match,
# case-insensitive), lacks (none may match), file (path, regex) the answer must have written.
MINI = [
    dict(id='ambient-thanks', ambient=True, history=_TEAM, author='Aiko', text='Thanks Marc!',
         check=dict(silent=True)),
    dict(id='ambient-note', ambient=True, history=_TEAM, author='Ken', text='Note to self: book the projector for Thursday.',
         check=dict(silent=True)),
    dict(id='ambient-greeting', ambient=True, history=[], author='Marc', text='Good morning everyone :)',
         check=dict(silent=True)),
    dict(id='ambient-question', ambient=True, history=_TEAM, author='Marc', text='When is the design review again?',
         check=dict(silent=False, has=[r'thu', r'2\s*(pm|:00|時)|14'])),
    dict(id='ambient-request', ambient=True, history=_TEAM, author='Aiko', text='Can someone list what each of us has to do before the launch?',
         check=dict(silent=False, has=[r'marc', r'budget', r'aiko', r'mockup', r'may\s*10|10\s*may|email'])),
    dict(id='fact-lookup', history=_TEAM, author='Marc', text='@mini what is the new launch date?',
         check=dict(silent=False, has=[r'may\s*17|17\s*may|17'], lacks=[r'may\s*3\b(?!.*17)'])),
    dict(id='summary', history=_TEAM, author='Ken', text='@mini summarize this conversation in 3 bullets',
         check=dict(silent=False, has=[r'thursday|thu', r'budget', r'17'])),
    dict(id='translate-ja', history=[], author='Marc', text='@mini translate into Japanese: "The meeting is cancelled, see you next week."',
         check=dict(silent=False, has=[r'[぀-ヿ一-鿿]', r'会議|ミーティング', r'来週'])),
    dict(id='date-math', history=[], author='Aiko', text='@mini if today is Monday 2026-11-16, what date is it in 10 days, and which weekday?',
         check=dict(silent=False, has=[r'2026-11-26|11/26|26\s*nov|nov\w*\s*26', r'thu'])),
    dict(id='convert', history=[], author='Ken', text='@mini how many minutes are 3.5 hours?',
         check=dict(silent=False, has=[r'\b210\b'])),
    dict(id='write-file', history=_TEAM, author='Ken', workspace=True,
         text='@mini save the action items from this chat to a file named launch-todo.md',
         check=dict(silent=False, file=('launch-todo.md', r'budget'), has=[r'launch-todo\.md'])),
    dict(id='no-invent', history=_TEAM, author='Aiko', text='@mini what time is the launch party?',
         check=dict(silent=False, has=[r"not|no |n't|unknown|unclear|didn|doesn|wasn|isn"])),
]
