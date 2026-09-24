"""Premium emoji — Translucent Pack (@devaiden), same as Outreach.

OS logos: keys os_* below → used as icon_custom_emoji_id on OS buttons
(see app/partner/fake.py → os_group_icon → keyboards._ib).

Animated country flags: put custom-emoji IDs from your sticker pack into
flag_germany / flag_finland / flag_poland. Unicode 🇩🇪 is NOT animatable —
only Telegram custom emoji (premium) can animate on buttons/text.
Until IDs are set, flag_* fall back to pin via icon_id().
"""

from __future__ import annotations

PACK: dict[str, str] = {
    "heart": "5278611606756942667",
    "folder": "5278227821364275264",
    "bookmark": "5276111746812112286",
    "lock": "5278602437001767574",
    "shield": "5276262671962892944",
    "warn": "5276240711795107620",
    "block": "5278578973595427038",
    "monitor": "5278647306525108244",
    "info": "5278753302023004775",
    "mega": "5278528159837348960",
    "check": "5278411813468269386",
    "cart": "5278613311858959074",
    "bag": "5276384644739129761",
    "clock": "5276412364458059956",
    "case": "5276037216244624892",
    "search": "5276395476646653290",
    "pin": "5276442772826515132",
    "user": "5275979556308674886",
    "users": "5298668674532538341",
    "term": "5276381204470329471",
    "wallet": "5276398496008663230",
    "at": "5278589204207528856",
    "crown": "5276229330131772747",
    "cube": "5278540791336165644",
    "link": "5278305362703835500",
    "hammer": "5276314275994954605",
    "gift": "5276422526350681413",
    "game": "5278304890257436355",
    "chart": "5278778882848220741",
    "home": "5278413853577346400",
    "robot": "5276127848644503161",
    "inbox": "5276220667182736079",
    "star": "5206476089127372379",
    "stack": "5206626000665868017",
    "up": "5206401524200145033",
    "down": "5206510891247371052",
    # OS logos — replace IDs with your pack's custom emoji
    "os_windows": "5357187187328688065",
    "os_ubuntu": "5300967525712929829",
    "os_debian": "5300808388584678952",
    "os_alma": "5359640494123000314",
    "os_rocky": "5300767358762098321",
    "os_centos": "5300758588438880795",
    "os_oracle": "5300965854970651009",
    "os_freebsd": "5300957668762987048",
    "os_linux": "5300957668762987048",
    "os_other": "5301233981189005137",
    # Animated flags (optional) — paste custom-emoji document_id from your pack:
    # "flag_germany": "….…",
    # "flag_finland": "….…",
    # "flag_poland": "….…",
}

_PLACEHOLDER = "😀"


def pe(name: str) -> str:
    eid = PACK.get(name) or PACK["cube"]
    return f'<tg-emoji emoji-id="{eid}">{_PLACEHOLDER}</tg-emoji>'


def icon_id(name: str) -> str:
    if name in PACK:
        return PACK[name]
    if name.startswith("flag_"):
        return PACK["pin"]
    return PACK["cube"]
