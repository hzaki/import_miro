#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script : push_storymap_r4_to_miro.py

But
----
Automatiser la création d'un Story Mapping pour la Release 4 du projet IT CALF
sur un board Miro existant :
- Crée un frame par THÈME (Epic)
- Crée des swimlanes par ACTIVITÉ (User Activities)
- Ajoute des Sticky Notes pour chaque USER STORY
- Colore et annote selon l'équipe (IHM / BPM / Métier/Finance) et le sprint

⚠️ Ce script est limité au périmètre du projet « IT CALF » et à la Release 4.

Entrées
-------
1) Variables d'environnement :
   - MIRO_TOKEN : jeton d'accès Miro (Bearer)
   - MIRO_BOARD_ID : identifiant du board cible

2) Un fichier CSV (optionnel mais recommandé) structuré ainsi :
   Theme,Activity,Story,Sprint,Team,Status,Notes
   Exemple de valeurs :
   Cartes Grises & Attestations, Réception & stockage, "Stocker l'attestation d'assurance", S2, IHM, En cours, "via EKIP360"

   Si aucun CSV n'est fourni, le script utilise une structure par défaut
   basée sur le besoin R4 communiqué (Cartes Grises, Paiement fournisseurs & prélèvements, Co-baillage, Syndication).

Sorties
-------
- Frames + sticky notes créés sur le board Miro
- Logs détaillés en console

Usage
-----
python push_storymap_r4_to_miro.py --board $MIRO_BOARD_ID --csv storymap_r4.csv --prefix R4

Dépendances
-----------
- Python 3.9+
- pip install requests python-slugify pandas (pandas uniquement si CSV utilisé)

Notes d'implémentation
----------------------
- Idempotence légère : le champ "--prefix" est préfixé aux titres des frames
  pour éviter les collisions. Rejouer avec le même prefix recréera toutefois
  les éléments (Miro n'offre pas d'upsert simple). Pour nettoyer, utiliser
  l'option --dry-run pour prévisualiser ou supprimer manuellement.
- Le placement est géré par une grille : chaque THÈME = colonne, chaque ACTIVITÉ = ligne.
- Les couleurs de sticky sont mappées par équipe.

Références API
--------------
- POST /v2/boards/{board_id}/frames
- POST /v2/boards/{board_id}/sticky_notes
- POST /v2/boards/{board_id}/shapes (utilisé pour titres / swimlanes)
- Doc Miro Board API v2 (schéma simplifié ici pour rester robuste)
"""

import os
import csv
import json
import time
import math
import argparse
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    raise SystemExit("Veuillez installer requests: pip install requests")

# --- Configuration visuelle et mapping projet IT CALF (Release 4) ---
TEAM_COLOR = {
    "IHM": "light_yellow",
    "BPM": "light_green",
    "Métier": "light_blue",
    "Metier": "light_blue",
    "Finance": "light_blue",
    "MO": "light_pink",
}

STATUS_EMOJI = {
    "Backlog": "⬜️",
    "À faire": "🟦",
    "En cours": "🟨",
    "Bloqué": "🟥",
    "À valider": "🟪",
    "Terminé": "✅",
}

# Dimensions et espacement (pixels Miro)
FRAME_W = 1800
FRAME_H = 1400
COL_GAP = 300
ROW_GAP = 200

STICKY_W = 220
STICKY_H = 140
STICKY_GAP_X = 40
STICKY_GAP_Y = 30

TITLE_HEIGHT = 100
LANE_HEIGHT = 260  # par activité

# Origine du story map sur le board
ORIGIN_X = -2000
ORIGIN_Y = -1000

# --- Modèle par défaut basé sur le périmètre R4 (projet IT CALF) ---
DEFAULT_MODEL = {
    "release": "R4",
    "themes": [
        {
            "name": "Cartes Grises & Attestations",
            "activities": [
                {
                    "name": "Réception & stockage",
                    "stories": [
                        {"title": "Réceptionner CG/attestation", "sprint": "S1", "team": "IHM", "status": "Backlog"},
                        {"title": "Stocker document avec métadonnées", "sprint": "S1", "team": "BPM", "status": "À faire"},
                        {"title": "Appariement auto doc⇄dossier", "sprint": "S2", "team": "BPM", "status": "Backlog"},
                    ],
                },
                {
                    "name": "Contrôles & anomalies",
                    "stories": [
                        {"title": "Détecter incohérences (EKIP/MO)", "sprint": "S2", "team": "BPM", "status": "Backlog"},
                        {"title": "File d'anomalies traitable", "sprint": "S3", "team": "Métier", "status": "Backlog"},
                    ],
                },
                {
                    "name": "Facturation de frais",
                    "stories": [
                        {"title": "Tracer frais liés CG", "sprint": "S3", "team": "Finance", "status": "Backlog"},
                    ],
                },
            ],
        },
        {
            "name": "Paiement fournisseurs & prélèvements clients",
            "activities": [
                {
                    "name": "Association d'infos externes",
                    "stories": [
                        {"title": "Associer libellés SEPA", "sprint": "S1", "team": "MO", "status": "Backlog"},
                        {"title": "Associer n° facture & annexes", "sprint": "S2", "team": "MO", "status": "Backlog"},
                    ],
                },
                {
                    "name": "Traçabilité & reporting",
                    "stories": [
                        {"title": "Traçabilité bout-en-bout", "sprint": "S3", "team": "MO", "status": "Backlog"},
                        {"title": "Standardiser reporting", "sprint": "S3", "team": "Finance", "status": "Backlog"},
                    ],
                },
            ],
        },
        {
            "name": "Co-baillage",
            "activities": [
                {
                    "name": "Création & gestion dossier",
                    "stories": [
                        {"title": "Créer dossier co-baillage", "sprint": "S1", "team": "IHM", "status": "Backlog"},
                        {"title": "Générer conventions", "sprint": "S2", "team": "BPM", "status": "Backlog"},
                        {"title": "Ajout docs au dossier", "sprint": "S2", "team": "IHM", "status": "Backlog"},
                    ],
                },
                {
                    "name": "Quote-parts & intégration EKIP360",
                    "stories": [
                        {"title": "Partager quote-parts", "sprint": "S3", "team": "Finance", "status": "Backlog"},
                        {"title": "Édition auto des contrats", "sprint": "S3", "team": "BPM", "status": "Backlog"},
                    ],
                },
            ],
        },
        {
            "name": "Syndication",
            "activities": [
                {
                    "name": "Contrats miroir & automatisation",
                    "stories": [
                        {"title": "Créer contrats miroir", "sprint": "S2", "team": "BPM", "status": "Backlog"},
                        {"title": "Générer contrats de syndication", "sprint": "S3", "team": "BPM", "status": "Backlog"},
                        {"title": "Calcul taux de refinancement", "sprint": "S3", "team": "Finance", "status": "Backlog"},
                    ],
                },
                {
                    "name": "Intégrations système",
                    "stories": [
                        {"title": "Intégrer EKIP360/MOCA", "sprint": "S3", "team": "MO", "status": "Backlog"},
                        {"title": "Réduire double-saisie", "sprint": "S3", "team": "IHM", "status": "Backlog"},
                    ],
                },
            ],
        },
    ],
}

# --- Helpers HTTP ---

def _auth_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _post(url: str, token: str, payload: Dict) -> Dict:
    resp = requests.post(url, headers=_auth_headers(token), data=json.dumps(payload))
    if resp.status_code >= 400:
        raise RuntimeError(f"POST {url} failed: {resp.status_code} {resp.text}")
    return resp.json()


# --- Miro primitives ---

def create_frame(board_id: str, token: str, title: str, x: float, y: float, w: int = FRAME_W, h: int = FRAME_H) -> str:
    url = f"https://api.miro.com/v2/boards/{board_id}/frames"
    payload = {
        "data": {"title": title},
        "position": {"x": x, "y": y},
        
    }
    out = _post(url, token, payload)
    return out.get("id")


def create_shape(board_id: str, token: str, text: str, x: float, y: float, w: int, h: int, shape: str = "rectangle") -> str:
    url = f"https://api.miro.com/v2/boards/{board_id}/shapes"
    payload = {
        "data": {"content": text, "shape": shape},
        "position": {"x": x, "y": y},
        "style": {"fontSize": 28},
    }
    out = _post(url, token, payload)
    return out.get("id")


def create_sticky(board_id: str, token: str, text: str, x: float, y: float, color: str = "light_yellow", w: int = STICKY_W, h: int = STICKY_H) -> str:
    url = f"https://api.miro.com/v2/boards/{board_id}/sticky_notes"
    payload = {
        "data": {"content": text},
        "style": {"fillColor": color, "textAlign": "left"},
        "position": {"x": x, "y": y},
    }
    out = _post(url, token, payload)
    return out.get("id")


# --- Placement logique ---

def compute_frame_origin(col_idx: int, row_idx: int = 0) -> Tuple[float, float]:
    x = ORIGIN_X + col_idx * (FRAME_W + COL_GAP)
    y = ORIGIN_Y + row_idx * (FRAME_H + ROW_GAP)
    return x, y


def lane_y(frame_y: float, lane_idx: int) -> float:
    return frame_y - FRAME_H / 2 + TITLE_HEIGHT + lane_idx * LANE_HEIGHT + LANE_HEIGHT / 2


def lane_title_y(frame_y: float, lane_idx: int) -> float:
    return frame_y - FRAME_H / 2 + TITLE_HEIGHT + lane_idx * LANE_HEIGHT + 30


def sticky_grid_positions(start_x: float, start_y: float, columns: int, count: int) -> List[Tuple[float, float]]:
    positions = []
    for i in range(count):
        row = i // columns
        col = i % columns
        x = start_x + col * (STICKY_W + STICKY_GAP_X)
        y = start_y + row * (STICKY_H + STICKY_GAP_Y)
        positions.append((x, y))
    return positions


# --- Chargement CSV optionnel ---

def load_from_csv(path: str) -> Dict:
    model: Dict[str, List] = {"release": "R4", "themes": []}
    themes: Dict[str, Dict] = {}

    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            theme = row.get("Theme", "Inconnu").strip()
            activity = row.get("Activity", "Général").strip()
            story = row.get("Story", "").strip()
            sprint = row.get("Sprint", "S1").strip()
            team = row.get("Team", "MO").strip()
            status = row.get("Status", "Backlog").strip()
            notes = row.get("Notes", "").strip()

            if theme not in themes:
                themes[theme] = {"name": theme, "activities": []}

            # trouve ou crée l'activité
            acts = themes[theme]["activities"]
            act = next((a for a in acts if a["name"].lower() == activity.lower()), None)
            if not act:
                act = {"name": activity, "stories": []}
                acts.append(act)

            title = story if not notes else f"{story}\n\n📝 {notes}"
            act["stories"].append({
                "title": title,
                "sprint": sprint,
                "team": team,
                "status": status,
            })

    model["themes"] = list(themes.values())
    return model


# --- Rendu du Story Map dans Miro ---

def render_storymap(board_id: str, token: str, model: Dict, prefix: str = "R4", dry_run: bool = False) -> None:
    themes = model.get("themes", [])

    for col_idx, theme in enumerate(themes):
        theme_name = theme.get("name", f"Theme {col_idx+1}")
        frame_title = f"{prefix} – {theme_name}"
        fx, fy = compute_frame_origin(col_idx)

        if dry_run:
            print(f"[DRY] Frame '{frame_title}' @ ({fx},{fy})")
        else:
            frame_id = create_frame(board_id, token, frame_title, fx, fy)
            print(f"Frame créé: {frame_title} -> {frame_id}")
            time.sleep(0.2)

        activities = theme.get("activities", [])
        for lane_idx, activity in enumerate(activities):
            act_name = activity.get("name", f"Activité {lane_idx+1}")
            # Titre de la lane (shape rect)
            title_x = fx - FRAME_W/2 + 200
            title_y = lane_title_y(fy, lane_idx)
            if dry_run:
                print(f"[DRY] Lane '{act_name}' title @ ({title_x},{title_y})")
            else:
                sid = create_shape(board_id, token, f"{act_name}", title_x, title_y, 600, 60, shape="round_rectangle")
                print(f"  Lane titre créé: {act_name} -> {sid}")
                time.sleep(0.15)

            # Zone de stickies pour cette lane
            stories = activity.get("stories", [])
            if not stories:
                continue

            # grille: 4 colonnes par défaut
            grid_columns = 4
            start_x = fx - FRAME_W/2 + 300
            start_y = lane_y(fy, lane_idx)
            positions = sticky_grid_positions(start_x, start_y, grid_columns, len(stories))

            for (story, (sx, sy)) in zip(stories, positions):
                sprint = story.get("sprint", "S?")
                team = story.get("team", "MO")
                status = story.get("status", "Backlog")
                color = TEAM_COLOR.get(team, "light_yellow")
                emoji = STATUS_EMOJI.get(status, "⬜️")
                text = f"{emoji} {story.get('title','Story')}\n[{team}] [{sprint}]"

                if dry_run:
                    print(f"[DRY] Sticky '{text[:40]}...' @ ({sx},{sy}) color={color}")
                else:
                    sid = create_sticky(board_id, token, text, sx, sy, color)
                    print(f"    Sticky créé: {sid} -> {text[:60]}...")
                    time.sleep(0.12)


# --- CLI ---

def parse_args():
    p = argparse.ArgumentParser(description="Publier le Story Mapping R4 (IT CALF) sur Miro")
    p.add_argument("--board", dest="board_id", default=os.getenv("MIRO_BOARD_ID"), help="ID du board Miro")
    p.add_argument("--token", dest="token", default=os.getenv("MIRO_TOKEN"), help="Token d'accès Miro (Bearer)")
    p.add_argument("--csv", dest="csv_path", default=None, help="Chemin CSV optionnel pour alimenter le story map")
    p.add_argument("--prefix", dest="prefix", default="R4", help="Préfixe pour les frames (idempotence légère)")
    p.add_argument("--dry-run", dest="dry", action="store_true", help="N'écrit pas dans Miro, affiche seulement")
    return p.parse_args()


def main():
    args = parse_args()
    if not args.board_id:
        raise SystemExit("Veuillez fournir --board ou définir MIRO_BOARD_ID")
    if not args.token:
        raise SystemExit("Veuillez fournir --token ou définir MIRO_TOKEN")

    if args.csv_path:
        print(f"Chargement du modèle depuis CSV: {args.csv_path}")
        model = load_from_csv(args.csv_path)
    else:
        print("Utilisation du modèle par défaut R4 (projet IT CALF)")
        model = DEFAULT_MODEL

    render_storymap(args.board_id, args.token, model, prefix=args.prefix, dry_run=args.dry)
    print("Terminé.")


if __name__ == "__main__":
    main()
