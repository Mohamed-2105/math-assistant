# Course assistant

A script that turns course material stored in a Google Drive folder into pages of a Notion database. Gemini reads the material and writes the sheets. The script repairs the formulas and creates the pages. It reads a folder on your computer that is kept in sync with your Drive, for example by Google Drive for desktop: it does not use the Google Drive API.

## What it does

The script looks in the `01_A_Traiter` folder. Missing folders are created at the start of a run.

```
assistant-cours/            the folder set in DRIVE_ROOT
├── 01_A_Traiter/
│   ├── Cours/              course PDFs
│   ├── Exercices/          exercise PDFs
│   ├── Corriges/           their corrections
│   ├── Labs/               labs
│   ├── Labs_Corriges/      their corrections
│   └── Notes_Manuscrites/  photos of the board
└── 02_Archives/            processed files
```

| Folder | What goes in | Page created in Notion |
|---|---|---|
| `Cours` | course PDFs | category "Cours fiché": an explanation of the course in parts, with its definitions, theorems, key concepts, formulas, algorithms and examples |
| `Exercices` and `Corriges` | an exercise PDF and its correction PDF | category "PC": the exercises with detailed corrections and the methods to remember |
| `Labs` and `Labs_Corriges` | a lab and its correction (.ipynb, .rmd, .html or .pdf) | category "Lab": questions, code and explanations |
| `Notes_Manuscrites` | photos of the board (.png, .jpg, .jpeg, .heic, .pdf) with the date in the file name | category "Notes de cours" or "PC", chosen from the timetable |

A processed file is moved to `02_Archives`. A file whose processing fails stays where it is, and the run goes on with the next one.

## How it works

```
Drive folder 01_A_Traiter
        |
        v
sources.py          reads notebooks, .rmd and .html as text, reads the date in the photo names
gemini_client.py    uploads the PDFs and the photos to Gemini
        |
        v
prompts.py          builds the prompt
schemas.py          gives the shape of the sheet
gemini_client.py    gets the sheet from Gemini (retries, fallback between models)
        |
        v
latex_cleaner.py    repairs the formulas
code_checks.py      warns when the code of a lab looks wrong
notion_blocks.py    builds the Notion blocks
katex_validator.py  checks every formula with KaTeX
        |
        v
notion_api.py       creates the page
pipeline.py         moves the files to 02_Archives
```

## Setup

You need Python 3.12 or newer (the tests were run on 3.12), a Notion integration that has access to your database, a Gemini API key and a Drive folder that is synced to your computer. Node.js is optional: it checks the formulas (see "Reliability").

```bash
pip install -r requirements.txt
cp .env.example .env
```

Optional, to check the formulas with KaTeX (needs Node.js):

```bash
npm install
```

Fill `.env`. The names are explained in `.env.example`, and `DRIVE_ROOT` is the folder that holds `01_A_Traiter` and `02_Archives`. The Notion database needs a title column, a `Cours` column and a `Catégorie` column (`Categorie` and `Type` also work). The last two must be of type select or multi-select.

Two lists describe your own studies and are written in `settings.py`:

- `COURS`: your courses, each with a short description. Gemini compares the content of a document with these descriptions to choose the course. The names must be the values of the `Cours` column in Notion.
- `PLANNING`: your weekly timetable. It must use the names of `COURS`.

## Run

```bash
python main.py
```

Run it again whenever new files are in the folders. Use `python3` if your system has no `python`.

## Choose the models

The models in `GEMINI_MODELS` (in `.env`) are tried in order, so put the best one first. To see which models your key can use (`--filtre abc` keeps the names that contain "abc"):

```bash
python diagnostic_gemini.py --lister
```

To try one model for a single run, give it on the command line, which wins over `.env`:

```bash
GEMINI_MODELS=model-name python main.py
```

`python diagnostic_gemini.py` tells whether each model sends a summary of its thoughts, and `--pdf path/to/course.pdf` shows how long each model stays silent on a real course. Nothing in your Drive or in Notion is touched. To make a model think more, set `NIVEAU_REFLEXION` (`minimal`, `low`, `medium` or `high`) or `BUDGET_REFLEXION` (a number of tokens, -1 lets the model decide, 0 turns thinking off) in `.env`. Which one a model understands depends on it: the diagnostic prints "jetons de réflexion annoncés par Gemini", which should grow with the setting. More thinking is slower and can cost more.

## Tests

```bash
python -m unittest
```

The tests need no key and no network. They replace Notion, Gemini and the PDF reader with fakes (`tests/fakes.py`) and set every setting themselves, so an existing `.env` does not change their result. The only value read from `.env` is `PRIVATE_WORDS`, by `tests/test_privacy.py`. When KaTeX is installed, the real formula check is tested too.

## Code map

| File | Role |
|---|---|
| `main.py` | entry point: checks the settings, then runs the pipeline |
| `pipeline.py` | the four processing steps and their order |
| `settings.py` | reads `.env`, holds the courses, the timetable and the limits, hides secrets in the output |
| `schemas.py` | the shape of each sheet that Gemini must return, and the instructions for the course, the title and the formulas |
| `prompts.py` | the text of the prompts |
| `sources.py` | reads input files, dates photos, finds the timetable slot |
| `gemini_client.py` | the Gemini client: file uploads, retries, model fallback, validation of answers |
| `latex_cleaner.py` | repairs the LaTeX and splits long equations |
| `code_checks.py` | warns when the code written for a lab looks incomplete or is invalid Python |
| `notion_blocks.py` | builds the Notion blocks from a sheet and cleans the page title |
| `notion_api.py` | reads the latest course page, creates pages, appends blocks |
| `katex_validator.py` | tests the equations of a page with KaTeX (`katex_check.js` runs it in Node.js and `package.json` gives its version) |
| `diagnostic_gemini.py` | lists the models of your key and tells what each one does |

## Rules in plain words

- **Course sheets.** Gemini reads the slides in order, writes an overview, then cuts the course into 3 to 8 parts. Each part starts with an introduction that says what the part does, why the teacher comes to it and where it leads. Each element (definition, theorem, key concept, formula, algorithm, example) gives its formal statement, copied in full, then an explanation: what each symbol stands for, what the statement says, why it holds, and an example from the course. The prompt contains one worked example of the level expected, marked as a model that must not be copied.
- **Course.** For a course, an exercise sheet or a lab, Gemini chooses the course by comparing the document with the descriptions in `COURS`. Neither the folder nor the file name is used. A refresher is chosen only for a document without application.
- **Exercises.** A sheet needs a correction in `Corriges` named `<name>_Correction.pdf`, `<name>_correction.pdf` or with the same file name as the sheet. A sheet without correction is skipped without a message.
- **Labs.** The correction is searched in `Labs_Corriges` as `<name>_Correction`, `<name>_correction` or `<name>`, followed by one of the extensions in `EXT_LABS`. Without one, the script prints a waiting message. Gemini rewrites the code of the correction cleanly, so it is not a copy. Two checks only print warnings: the share of the code lines of the correction found in the code of Gemini (a warning below `SEUIL_FIDELITE_CODE`), and the validity of the Python parts.
- **Notes.** The date of a photo comes from its file name only, in the form `YYYY-MM-DD_HH-MM-SS`. A photo without a valid date is left in place and a message names it. Photos in the same timetable slot of the same day make one page. A photo outside the timetable goes into one "free" session for that day, and Gemini chooses the course. For a known slot, the latest "Cours fiché" page of that course is given to Gemini so that the note uses the same notation. A second pass cleans the layout of the note, and if it fails the first version is kept. Audio files are not processed.
- **Titles.** A page title holds the subject only, because the course and the type of page are already in the `Cours` and `Catégorie` columns: at most `LIMITE_TITRE` characters, without catalogue number, teacher name, course name, type of document, date or numbering. The script removes dollar signs and cuts a longer title with an ellipsis. Numbering is left to you.
- **PDFs.** With `PDF_VIA_GEMINI` on (the default), a PDF is uploaded to Gemini, which reads the pages itself, formulas and figures included. The script deletes the uploaded file as soon as the request is done. Notebooks, `.rmd` and `.html` files are always read as text. With the setting off, the text of the PDF is extracted locally with `pymupdf4llm`, and `PDF_AVEC_OCR` decides whether pages without text are read with OCR.

## Reliability

- **Models and tries.** The models in `GEMINI_MODELS` are tried in order, each `TENTATIVES_PAR_MODELE` times, with `PAUSE_ENTRE_ESSAIS` seconds between two tries. An invalid answer or an empty sheet counts as a failed try. If every try fails, no page is created, the file stays where it is, a message names it and the run goes on.
- **Waiting.** The answer is read as a stream. A request is given up when no data arrives for `DELAI_APPEL_GEMINI` seconds, and an answer that never ends is stopped after `DUREE_MAX_REPONSE` seconds. The next model is then used at once. Messages show the progress and the silence. With `RESUME_PENSEES` on (the default), a model that thinks sends a summary of its thoughts, which keeps the silence away and never ends up in the sheet.
- **Formulas.** Gemini is told to put every formula between `$...$`. When it does not, the script puts that LaTeX between dollars itself, and a warning says how many extracts were found. Every formula is then checked by KaTeX, in the mode where it will be shown, and a refused one is written as plain text. Without Node.js or KaTeX, the check is skipped with one message.
- **Notion.** An equation of more than `LIMITE_EQUATION` characters is split, and a toggle holds at most `TAILLE_TOGGLE` blocks and continues in a "(suite n)" toggle. A page is created with its first 100 blocks and the rest is appended. If Notion refuses a page, the script tries again with the equations written as text, then with the title only, and if that fails the file stays in place.

## Security and privacy

- Secrets live in `.env`, which `.gitignore` excludes. `settings.py` is the only module that reads the environment.
- Everything the program prints goes through `log` in `settings.py`, which replaces the Notion token, the Gemini key and the database id by `***`.
- Your PDFs, lab files and note photos are sent to Gemini, and the generated pages are sent to Notion. PDFs and photos are uploaded to Gemini's file storage for the time of the request and deleted right after. If a deletion fails, a message says so, and according to Google's documentation the file expires by itself after 48 hours.
- `tests/test_privacy.py` fails on a string that looks like a Google or Notion key, on an email address that is not at `example.com` or `example.org`, and on any word listed in `PRIVATE_WORDS` in your own `.env`.
- Before the first push, search the whole git history for your address, your name and your keys, because deleting a file does not remove it from the history. If a key was ever committed, revoke it and create a new one.

## Known limits

- The quality of the sheets depends on the model that writes them. A lighter model follows the rules of the prompt less well (for example the one about putting every formula between dollar signs), explains less clearly than a stronger one, and might stop reading a PDF in the middle. The author uses a Gemini 3.5 model, chosen to keep the use of credits low: the default value of `GEMINI_MODELS` in `settings.py` starts with it. A stronger model can give better sheets, usually at a higher cost (see "Choose the models").
- Gemini makes three choices that nothing verifies. The course of a document must be one of the names in `COURS`, but a refresher and its main course can be mixed up. The title is cleaned of catalogue numbers and teacher names on request, which was not tested on real documents, and a LaTeX command can remain in it. The explanations follow a prompt that forbids inventing content, but only your own reading confirms that an explanation is right.
- Formulas are the part to check first. A model can write LaTeX without dollar signs: the program puts it between dollars with a set of rules, which can wrap too much or too little, and a formula made only of plain letters and digits, such as `y = x + 1`, stays as text. KaTeX then checks every formula, and a refused one is shown as text, not corrected. KaTeX 0.19.0 is used (`package.json`), the version of Notion is not known, and a command damaged into valid text passes the check. A page where a warning appeared deserves a quick look.
- The code of a lab is rewritten by Gemini and never run. The fidelity check only counts the lines of the correction that are missing, so it can warn about code that is right (a renamed variable) and does not see invented lines. Only the Python parts are checked for syntax, and the check is skipped when the correction is a PDF.
- A model that reads a long PDF, or thinks before it writes, can be silent for minutes. The silence limit is 400 seconds by default (`DELAI_APPEL_GEMINI`), so a sheet whose two models both go silent costs about 13 minutes, and nothing stops the whole run early when Gemini is down. Google counts about 258 tokens per PDF page (an average, higher for complex pages) and accepts at most 50 MB and 1000 pages per PDF.
- A file with the same name as one already in `02_Archives` can replace it. On a case-insensitive file system such as the macOS default, a correction named `<name>_correction` is archived as `<name>_Correction`.
- With `PDF_VIA_GEMINI` off, the text of a PDF is extracted locally: reading 150 slides can take about a minute, formulas can be scrambled (a square root can become a replacement character), and a scanned PDF gives an empty sheet, so no page is created. OCR is optional with `PDF_AVEC_OCR` and was not tested here.
- The prompts and the messages are in French. The lab prompt in `prompts.py` mentions a master's degree by name: change it to your own.