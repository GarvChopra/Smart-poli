"""
Common Indian branded and generic medicine names for validation.
This list includes ~200 commonly prescribed medicines in India.

Used as the RapidFuzz reference set for name_confidence (see parser.py).
Manufacturer/company names are deliberately excluded — a fuzzy match against
a company name (e.g. "Cipla") would be scored as a confident medicine-name
match even though it names no medicine, which is exactly the kind of wrong
confidence CLAUDE.md's confidence gate exists to prevent.
"""

INDIAN_DRUG_NAMES = {
    # Paracetamol variants
    "paracetamol", "crocin", "dolo", "dolo 650", "calpol", "paracip", "metacin", "pyrigesic",
    "pacimol", "fepanil", "sumo", "combiflam", "saridon", "disprin",
    
    # Antibiotics
    "azithral", "zithromax", "augmentin", "amoxyclav", "cifran", "ciplox", "norflox",
    "ofloxacin", "levofloxacin", "cefixime", "cefalexin", "amoxicillin", "ampicillin",
    "erythromycin", "clarithromycin", "doxycycline", "tetracycline", "metronidazole",
    "tinidazole", "ornidazole", "secnidazole", "clindamycin", "lincomycin",
    
    # Antacids and gastric medicines
    "pantop", "pan 40", "omez", "razo", "gelusil", "eno", "digene", "pantoprazole",
    "omeprazole", "rabeprazole", "esomeprazole", "lansoprazole", "famotidine",
    "ranitidine", "domperidone", "ondansetron", "sucralfate", "simethicone",
    
    # Vitamins and supplements
    "becosules", "neurobion", "revital", "shelcal", "calcirol", "vitamin d3",
    "folic acid", "iron", "calcium", "zinc", "magnesium", "multivitamin",
    "b complex", "vitamin c", "vitamin e", "cod liver oil", "omega 3",
    
    # Diabetes medicines
    "metformin", "glycomet", "januvia", "galvus", "glimepiride", "gliclazide",
    "glipizide", "pioglitazone", "sitagliptin", "vildagliptin", "insulin",
    "mixtard", "actrapid", "lantus", "humalog", "novomix", "glucobay",
    
    # Blood pressure medicines
    "telma", "amlodipine", "stamlo", "telmisartan", "losartan", "atenolol",
    "metoprolol", "propranolol", "enalapril", "lisinopril", "ramipril",
    "nifedipine", "diltiazem", "verapamil", "hydrochlorothiazide", "furosemide",
    
    # Pain and inflammation
    "voveran", "diclofenac", "combiflam", "ibuprofen", "brufen", "aspirin",
    "nimesulide", "aceclofenac", "etoricoxib", "celecoxib", "indomethacin",
    "piroxicam", "naproxen", "ketorolac", "tramadol", "paracetamol",
    
    # Antihistamines and allergies
    "cetrizine", "allegra", "montair", "montelukast", "fexofenadine",
    "loratadine", "chlorpheniramine", "pheniramine", "hydroxyzine",
    "desloratadine", "levocetirizine", "bambuterol", "salbutamol",
    
    # Cough and cold
    "benadryl", "corex", "ascoril", "alex", "grilinctus", "dextromethorphan",
    "guaifenesin", "bromhexine", "ambroxol", "terbutaline", "theophylline",
    
    # Antifungals
    "fluconazole", "itraconazole", "ketoconazole", "terbinafine", "griseofulvin",
    "nystatin", "amphotericin", "clotrimazole", "miconazole",
    
    # Cardiac medicines
    "digoxin", "isosorbide", "nitroglycerin", "clopidogrel", "aspirin",
    "warfarin", "heparin", "streptokinase", "atorvastatin", "rosuvastatin",
    "simvastatin", "pravastatin", "fenofibrate", "gemfibrozil",
    
    # Thyroid medicines
    "thyroxine", "eltroxin", "thyronorm", "carbimazole", "methimazole",
    "propylthiouracil", "levothyroxine", "liothyronine",
    
    # Psychiatric medicines
    "fluoxetine", "sertraline", "paroxetine", "escitalopram", "amitriptyline",
    "imipramine", "clomipramine", "lithium", "haloperidol", "risperidone",
    "olanzapine", "quetiapine", "aripiprazole", "lorazepam", "diazepam",
    "alprazolam", "clonazepam", "zolpidem", "zopiclone",
    
    # Eye and ear drops
    "moxifloxacin", "tobramycin", "gentamicin", "ciprofloxacin", "ofloxacin",
    "prednisolone", "dexamethasone", "timolol", "latanoprost", "brimonidine",
    
    # Skin medicines
    "betnovate", "panderm", "quadriderm", "soframycin", "neosporin",
    "mupirocin", "fusidic acid", "hydrocortisone", "calamine", "lacto calamine",

    # Additional common Indian brands (PS-cited names + frequently seen scrips)
    "pan-d", "pan d", "montek", "montek lc", "zerodol", "zerodol sp", "cetzine",
    "metrogyl", "metrogyl 400", "dolonex", "flexon", "sinarest", "d cold",
    "cheston cold", "wikoryl", "azee", "monocef", "taxim", "taxim o", "zifi",
    "moxikind cv", "moxclav", "unienzyme", "digeplex", "cyclopam", "meftal",
    "meftal spas", "drotin", "buscopan", "perinorm", "emeset", "zofer",
    "vertin", "stugeron", "avomine", "electral", "orsl", "enterogermina",
    "sporlac", "econorm", "lactobacillus", "liv 52", "udiliv", "hepamerz",
    "evion", "limcee", "zincovit", "supradyn", "a to z", "polybion",
    "duphaston", "susten", "meprate", "unwanted 72", "i-pill",
    "amlokind", "cardace", "ecosprin", "clopilet", "rosuvas", "storvas",
    "atorva", "istamet", "janumet", "glycigon", "gemer", "amaryl",
    "human mixtard", "levera", "ativan", "restyl", "nexito", "prosom",
    "pacitane", "serenace", "depsonil", "sizodon", "oleanz", "asendin",
    "wysolone", "omnacortil", "deriphyllin", "asthalin", "budecort",
    "foracort", "seroflo", "duolin", "levolin", "aerocort", "otrivin",
    "nasivion", "sinclar", "rhinocort", "candid", "canesten", "terbicip",
    "foltas", "act", "chlorhexidine", "framycetin", "otogesic", "candibiotic",
    "genticyn", "moxicip", "vigamox", "refresh tears", "systane", "brolene",
    "laxatives", "cremaffin", "duphalac", "dulcolax", "lactulose", "bisacodyl",
    "loperamide", "eldoper", "racecadotril", "zerdogut", "torsion", "lasix",
    "aldactone", "spironolactone", "digoxin injection", "nitrocontin",
    "sorbitrate", "monotrate", "ecospirin", "asprin gr", "clexane",
    "enoxaparin", "acitrom", "acenocoumarol", "target", "pregabalin",
    "gabapentin", "neurogab", "pregeb", "duzela", "nexito plus",
    "etilaam", "calmpose", "restyl md", "hifenac", "acecloren",
    "mobizox", "flexura", "movexx", "dolo 650 mg", "crocin advance",
    "combiflam plus", "calpol 500",
}