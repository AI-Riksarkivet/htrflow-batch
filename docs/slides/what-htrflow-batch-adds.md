---
marp: true
theme: riksarkivet
paginate: false
lang: sv
---

# Pipelinen är kvar, kampanjen är ny

![w:1120](assets/intro-pipeline-campaign.svg)

**Inget ändras i hur en sida läses.** Pipelinen är samma htrflow-recept som förut. Det nya är kampanjen: en kort lista över arkivvolymer, och vilket recept som ska köras på dem.

<!--
Övre raden är htrflow som alla har använt det: ett recept, en mapp med sidor
på en maskin. Nedre raden är det htrflow-batch lägger till: i stället för en
mapp lämnar man in en lista med volymer, angivna med referenskod, och
plattformen gör resten. En volym är en arkivvolym, en inbunden enhet av
sidor, aldrig en disk.
-->

---

# En maskin, eller många

![w:1060](assets/intro-one-or-many.svg)

**Samma recept, samma text — på så många maskiner som det finns GPU:er.** Sidorna hämtas från arkivets bildserver; varje resultat hamnar på ett gemensamt ställe.

<!--
Vänster: i dag väntar varje volym på den före, på en GPU, och går maskinen
sönder börjar man om. Höger: volymerna körs sida vid sida, en per maskin;
inget ligger på någon enskild maskins disk, så en volym vars maskin går
sönder fortsätter helt enkelt på en annan, från den sida där den stannade.
-->

---

# Kubernetes kör det, Kueue avgör när

![w:1120](assets/intro-kueue.svg)

**Kubernetes** får många maskiner att uppträda som en dator: en volym hamnar där en GPU är ledig. **Kueue** är kön framför: en kampanj väntar tills de GPU:er den behöver är lediga, den brådskande går först, och varje kampanj kan pausas och återupptas.

<!--
Kubernetes är det öppna system som de flesta moln kör på; poängen för det
här rummet är bara att ingen väljer maskin för hand och att förlorat arbete
startas om. Kueue är den del som hindrar alla från att ta alla GPU:er på en
gång: kampanjerna står i kö, kön räknar lediga GPU:er och släpper fram nästa
bara när den får plats i sin helhet. Här är två GPU:er lediga, så C, som
behöver en, startar före B, som behöver fyra.
-->

---

# Statussidan, och viewern

<div class="cols wide-left">
<div>

![w:620](assets/part-1-status-page.png)

**Ett kort per kampanj**, där varje volyms framsteg räknas medan den körs, och allt som gick fel står med namn.

</div>
<div>

![w:420](assets/intro-viewer.svg)

**Varje sida, varje rad, dess text** — öppen medan volymen fortfarande körs.

</div>
</div>

<!--
Statussidan är det enda en läsare behöver titta på: pågående kampanjer
först, sedan det som behöver uppmärksamhet, sedan de färdiga. En volyms namn
öppnar den i viewern, Riksarkivets egen Universal Viewer: sidbilden med varje
transkriberad rad markerad och texten bredvid, sida för sida, så snart de
första sidorna är klara.
-->
