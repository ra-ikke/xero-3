"""Forum tag metadata (ported from legacy JS resources)."""

# This is kept as a direct data port because it includes many Discord-specific IDs.
TAGS_LIST: list[dict] = [
    {
        "name": "rotation",
        "description": "rotation tags",
        "tags": [
            {"id": "1130149859928838224", "name": "Closed", "moderated": False, "emoji": {"id": "1130135955605565451", "name": None}},
            {"id": "1130150711972339743", "name": "Open", "moderated": False, "emoji": {"id": "1130152512914198608", "name": None}},
            {"id": "1130149453760835584", "name": "Perm", "moderated": False, "emoji": {"id": "1130146872124776458", "name": None}},
            {"id": "1130149617409998848", "name": "Deperm", "moderated": False, "emoji": {"id": "1130146688317784205", "name": None}},
            {"id": "1130149647520890950", "name": "Edit", "moderated": False, "emoji": {"id": "1130146874939166750", "name": None}},
            {"id": "1130149683235405844", "name": "Other", "moderated": False, "emoji": {"id": "1130135953718128773", "name": None}},
            {"id": "1128460750231441488", "name": "Shaman (P4)", "moderated": True, "emoji": {"id": "1130135963046256730", "name": None}},
            {"id": "1128460901872316459", "name": "Art (P5)", "moderated": True, "emoji": {"id": "1130135964493291591", "name": None}},
            {"id": "1130136771796140073", "name": "Mechanism (P6)", "moderated": False, "emoji": {"id": "1130135967106342944", "name": None}},
            {"id": "1130137083852374118", "name": "No Shaman (P7)", "moderated": False, "emoji": {"id": "1130135969513873419", "name": None}},
            {"id": "1130136599804518524", "name": "Double Shaman (P8)", "moderated": True, "emoji": {"id": "1130135972726710292", "name": None}},
            {"id": "1130137320058802226", "name": "Miscellaneous (P9)", "moderated": False, "emoji": {"id": "1130135975058755715", "name": None}},
            {"id": "1130136771796140073", "name": "Mechanism no Shaman (P12)", "moderated": False, "emoji": {"id": "1130135967106342944", "name": None}},
        ],
    },
    {
        "name": "bootcamp",
        "description": "bootcamp tags",
        "tags": [
            {"id": "1131762333866266635", "name": "Closed", "moderated": False, "emoji": {"id": "1130135955605565451", "name": None}},
            {"id": "1131762406381592659", "name": "Open", "moderated": False, "emoji": {"id": "1130152512914198608", "name": None}},
            {"id": "1131762444025466962", "name": "Perm", "moderated": False, "emoji": {"id": "1130146872124776458", "name": None}},
            {"id": "1131763005042987138", "name": "Edit", "moderated": False, "emoji": {"id": "1130146874939166750", "name": None}},
            {"id": "1131763050400194673", "name": "Deperm", "moderated": False, "emoji": {"id": "1130146688317784205", "name": None}},
            {"id": "1131763092922048542", "name": "Other", "moderated": False, "emoji": {"id": "1130135953718128773", "name": None}},
            {"id": "1131763612680208509", "name": "Bootcamp (P3)", "moderated": False, "emoji": {"id": "1130135960307380244", "name": None}},
            {"id": "1132005531062648852", "name": "Batch", "moderated": False, "emoji": {"id": "1130152517309825044", "name": None}},
        ],
    },
    {
        "name": "racing",
        "description": "racing tags",
        "tags": [
            {"id": "1131761683686248498", "name": "Closed", "moderated": False, "emoji": {"id": "1130135955605565451", "name": None}},
            {"id": "1131761746630148178", "name": "Open", "moderated": False, "emoji": {"id": "1130152512914198608", "name": None}},
            {"id": "1131761827404070914", "name": "Perm", "moderated": False, "emoji": {"id": "1130146872124776458", "name": None}},
            {"id": "1131761873054871683", "name": "Edit", "moderated": False, "emoji": {"id": "1130146874939166750", "name": None}},
            {"id": "1131761928272887870", "name": "Deperm", "moderated": False, "emoji": {"id": "1130146688317784205", "name": None}},
            {"id": "1131761962078970028", "name": "Other", "moderated": False, "emoji": {"id": "1130135953718128773", "name": None}},
            {"id": "1131761625850970173", "name": "Racing (P17)", "moderated": False, "emoji": {"id": "1130135984307179610", "name": None}},
        ],
    },
    {
        "name": "survivor",
        "description": "survivor tags",
        "tags": [
            {"id": "1131763802799611914", "name": "Closed", "moderated": False, "emoji": {"id": "1130135955605565451", "name": None}},
            {"id": "1131763829563465888", "name": "Open", "moderated": False, "emoji": {"id": "1130152512914198608", "name": None}},
            {"id": "1131763860081213470", "name": "Perm", "moderated": False, "emoji": {"id": "1130146872124776458", "name": None}},
            {"id": "1131763890749972500", "name": "Edit", "moderated": False, "emoji": {"id": "1130146874939166750", "name": None}},
            {"id": "1131763917727727657", "name": "Deperm", "moderated": False, "emoji": {"id": "1130146688317784205", "name": None}},
            {"id": "1131763992872878080", "name": "Other", "moderated": False, "emoji": {"id": "1130135953718128773", "name": None}},
            {"id": "1131764137475706981", "name": "Survivor (P10)", "moderated": False, "emoji": {"id": "1130135977168474145", "name": None}},
            {"id": "1131764213187100724", "name": "Vampire Surv (P11)", "moderated": False, "emoji": {"id": "1130135979219497040", "name": None}},
            {"id": "1131764638376271872", "name": "Dual Surv (P24)", "moderated": False, "emoji": {"id": "1131764457761161286", "name": None}},
        ],
    },
    {
        "name": "defilante",
        "description": "defilante tags",
        "tags": [
            {"id": "1268206181525225576", "name": "Closed", "moderated": False, "emoji": {"id": "1130135955605565451", "name": None}},
            {"id": "1268206463122145448", "name": "Open", "moderated": False, "emoji": {"id": "1130152512914198608", "name": None}},
            {"id": "1268206510622773271", "name": "Perm", "moderated": False, "emoji": {"id": "1130146872124776458", "name": None}},
            {"id": "1268206569204482171", "name": "Edit", "moderated": False, "emoji": {"id": "1130146874939166750", "name": None}},
            {"id": "1268206680466919485", "name": "Deperm", "moderated": False, "emoji": {"id": "1130146688317784205", "name": None}},
            {"id": "1268206744824320040", "name": "Other", "moderated": False, "emoji": {"id": "1130135953718128773", "name": None}},
            {"id": "1268206812143026268", "name": "Defilante (P18)", "moderated": False, "emoji": {"id": "1130135987473891429", "name": None}},
        ],
    },
]


TAGS_BY_GROUP: dict[str, dict[str, dict]] = {
    group["name"]: {tag["name"].lower(): tag for tag in group.get("tags", [])}
    for group in TAGS_LIST
}

