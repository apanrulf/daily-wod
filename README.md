# Dagens WOD-agent

En AI-agent som driver ett Instagramkonto med ett nytt träningspass (WOD) varje dag. Claude är coachen och allt körs gratis i GitHub Actions.

<img src="exempel.jpg" width="360" alt="Exempel på en WOD-bild">

**Vad den gör varje morgon**
1. **Skriver dagens pass.** Coachen följer veckoschemat i `varumarke.md` och tittar på de senaste tre veckornas pass, så att rörelser, tidsdomäner och muskelgrupper varierar. Varje pass har vikter i kg och lb och skalningsalternativ.
2. **Gör en bild.** Passet ritas upp som en bild i formatet 1080 × 1350.
3. **Publicerar på Instagram.** Inlägget får en bildtext med passet, coachens kommentar och hashtags, och uppmanar folk att lägga sina resultat i kommentarerna.

Alla pass skrivs från grunden av agenten. Den kopierar aldrig andras pass och använder aldrig ordet "CrossFit", som är ett skyddat varumärke.

---

## Det här behöver du

- Ett Instagramkonto av typen **Business** eller **Creator**. Välj ett namn utan ordet "CrossFit", till exempel *dailywod.engine*.
- Ett Facebook-konto för att logga in på Meta for Developers.
- Ett GitHub-konto. Det är gratis.
- En API-nyckel till Claude. API:t debiteras per användning, separat från en Claude-prenumeration.

---

## Installation

### 1. Skapa Instagramkontot
Skapa kontot och byt till ett professionellt konto (Business eller Creator) under kontoinställningarna i appen.

### 2. Skapa en Meta-app och hämta en token
1. Gå till [developers.facebook.com/apps](https://developers.facebook.com/apps) och klicka **Skapa app**. Välj typen **Business**.
2. Lägg till produkten **Instagram** och öppna **API setup with Instagram business login**.
3. Lägg till ditt Instagramkonto på sidan och följ instruktionerna där.
4. Se till att appen har behörigheterna `instagram_business_basic`, `instagram_business_content_publish` och `instagram_business_manage_comments`.
5. Klicka **Generate token**, logga in och kopiera token. Den gäller i 60 dagar och förnyas automatiskt i steg 8.

Du behöver ingen App Review så länge appen bara används för konton du själv äger och har lagt till i appen.

### 3. Skaffa en Claude API-nyckel
Logga in på [platform.claude.com](https://platform.claude.com), lägg till betalning och skapa en nyckel under **API keys**. Ett WOD om dagen kostar bara några få anrop.

### 4. Skapa ett publikt GitHub-repo
1. Gå till [github.com/new](https://github.com/new), döp repot till till exempel `daily-wod` och välj **Public**.
   Repot måste vara publikt eftersom Instagram hämtar bilderna därifrån. Bara koden, bilderna och passen syns. Dina nycklar ligger som hemligheter i steg 5 och är aldrig synliga.
2. Ladda upp alla filer från den här mappen med **Add file → Upload files**.
   Mappen `.github` och filerna `.gitignore` och `.env.example` är dolda på datorn. På Mac visar du dem i Finder med **Cmd + Shift + .**, i Windows under **Visa → Dolda objekt**. Utan `.github` körs ingenting.

### 5. Lägg in hemligheterna
I repot: **Settings → Secrets and variables → Actions → New repository secret**. Lägg till:

| Namn | Värde |
|---|---|
| `ANTHROPIC_API_KEY` | Nyckeln från steg 3 |
| `IG_ACCESS_TOKEN` | Token från steg 2 |

### 6. Anpassa coachen
Öppna `varumarke.md` i repot och klicka på pennan för att redigera. Det går bra från mobilen. Filen är redan ifylld för ett engelskspråkigt konto, men ändra gärna veckoschemat, utrustningen, tonen och hashtagsen.

### 7. Provkör
Gå till fliken **Actions**, välj **Dagens WOD** och klicka **Run workflow**. Efter ett par minuter ligger dagens pass på Instagram. Därefter körs det automatiskt varje morgon.

### 8. Automatisk förnyelse av token
Token från Meta slutar fungera efter 60 dagar. Flödet *Förnya Instagram-token* förnyar den varje måndag, men det behöver lov att spara den nya token:
1. Gå till GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Välj bara ditt WOD-repo. Under **Repository permissions** sätter du **Secrets** till **Read and write**.
3. Skapa token och lägg in den som en hemlighet i repot med namnet `GH_PAT`, på samma sätt som i steg 5.
4. Provkör flödet *Förnya Instagram-token* under **Actions**. Token måste vara minst ett dygn gammal innan den kan förnyas.

---

## Ändra publiceringstiden
Öppna `.github/workflows/dagens-wod.yml` och ändra raden `cron: "17 5 * * *"`. Tiden anges i UTC, så 05:17 UTC är 07:17 svensk sommartid och 06:17 vintertid. Vid hög belastning hos GitHub kan schemalagda körningar bli några minuter sena.

## Om något går fel
GitHub mejlar dig när ett schemalagt flöde misslyckas. Öppna körningen under **Actions** för att se felet.

| Fel | Lösning |
|---|---|
| `Invalid OAuth access token` | Token har gått ut. Hämta en ny enligt steg 2 och byt ut hemligheten `IG_ACCESS_TOKEN`. Kontrollera sedan steg 8. |
| `Bilden går inte att nå på den publika adressen` | Repot är troligen privat. Gör det publikt under **Settings → General → Danger Zone**. |
| `Saknar ANTHROPIC_API_KEY` | Hemligheten saknas eller är felstavad. |
| `Hemligheten GH_PAT saknas` | Gör steg 8. |
| `Dygnsgränsen är nådd` | Instagram tillåter högst 100 publiceringar per dygn via API:t. Det händer bara om något körts väldigt många gånger. |
| Schemat har slutat köras | GitHub pausar schemalagda flöden i publika repon efter 60 dagar utan aktivitet. Aktivera flödet igen under **Actions**. |

---

## På din egen dator

Du kan prova agenten lokalt, till exempel för att se hur passen och bilderna blir innan du kopplar på GitHub.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fyll i ANTHROPIC_API_KEY och IG_ACCESS_TOKEN

python main.py status                        # kontrollera kopplingen
python main.py wod-skapa --torrkorning       # dagens pass och bild, sparas inte
python main.py wod-skapa                     # som ovan men du kan ge feedback och spara
```
Bilden hamnar i mappen `bilder`. Med `--torrkorning` publiceras ingenting.

### Svara på kommentarer
Agenten kan också gå igenom kommentarer, svara på frågor och gratulera till bra tider. Den flaggar sådant som du själv behöver ta i `flaggade.md`.
```bash
python main.py kommentarer               # du godkänner varje svar
python main.py kommentarer --auto        # svarar själv
```

### Andra inlägg
Vill du lägga upp egna bilder, till exempel från en träning, lägger du dem i `ko.json` och kör `python main.py posta`. Agenten skriver då bildtexten.

---

## Filerna

| Fil | Vad den är |
|---|---|
| `varumarke.md` | Coachens instruktioner: ton, veckoschema, utrustning, regler |
| `wods.json` | Alla pass som skapats, nyast först. Coachen använder dem för att variera. |
| `bilder/` | Bilderna som Instagram hämtar |
| `main.py` | Kommandona: `status`, `wod-skapa`, `wod-publicera`, `kommentarer`, `posta`, `fornya-token` |
| `wod.py` | Coachen som skriver passen |
| `bild.py` | Ritar bilderna |
| `agent.py` | Bildtexter för egna bilder och kommentarsagenten |
| `instagram_api.py` | Anropen till Instagrams API |
| `.github/workflows/` | De schemalagda flödena i GitHub Actions |
| `fonts/` | Typsnitten Bebas Neue och Barlow (fri licens, SIL OFL) |
