# Première mission Codex — audit fiable et protection des collecteurs SEC

## Objectif
Auditer le pipeline public du dépôt `raiux/iic-sec-data`, puis corriger uniquement les défauts démontrés du collecteur SEC / cache, sans altérer le prototype de valorisation VINCI ni le moteur DCF existants. Cette mission est volontairement limitée à la fondation de données.

## Phase 1 — lecture seule
1. Cartographier `scripts/`, `tests/`, `.github/workflows/` et les fichiers `data/` (identifier aussi doublons/structures anormales, sans suppression).
2. Vérifier `scripts/collect_sec.py` et `.github/workflows/collect-sec.yml`: User-Agent, temporisation >= 1 s entre requêtes, cache par société, invalidation/fraîcheur, politique HTTP 403/429/5xx, timeout, atomicité des écritures, conservation de la dernière valeur valide, période/devise/concept et exclusions de faits inadaptés.
3. Vérifier dans les fichiers existants la collecte des pilotes Cameco (IFRS) et Microsoft (US-GAAP); expliquer la couverture réelle, les dates, les défauts et les champs manquants. Ne pas prétendre avoir vérifié SEC en direct sans l'avoir fait.
4. Exécuter `python -m unittest discover -s tests -p 'test_*.py' -v`. Produire une liste priorisée des risques et un plan de correction minimal.

## Phase 2 — implémentation contrôlée
- Corriger uniquement les risques P0/P1 confirmés sur une nouvelle branche; ne pas reconfigurer les workflows ou changer le format des résultats sans test de compatibilité.
- Ajouter des tests entièrement hors réseau pour: intervalle de requêtes, 403, 429 Retry-After, 5xx, cache valide/périmé, réponse malformée et rétention de la dernière observation valide. Isoler les appels réseau et l'horloge si nécessaire pour les tester de façon déterministe.
- Définir précisément ce que signifie `valid`: ne pas traiter automatiquement tout JSON téléchargé comme une preuve comptable normalisée.
- Préserver les résultats VINCI 2025 et leurs avertissements: la valorisation par BPA/PER est illustrative, non un DCF ni un objectif de cours validé.

## Critères d'acceptation
- Une PR ciblée, aucune modification directe de `main`.
- Tests existants + nouveaux tests réussis, avec sortie/commandes documentées.
- Aucun accès réseau dans les tests unitaires; essais SEC réels optionnels et explicitement identifiés.
- Aucun secret, données personnelles, valeur inventée, action boursière, API payante.
- Statuts d'erreur et fraîcheur lisibles; pas d'écrasement silencieux du dernier jeu de données valides.
- Compte rendu en français: confirmé / corrigé / non résolu / prochaine étape.

## Après cette PR (hors périmètre)
Normalisation IFRS/US-GAAP commune; contrôles comptables vérifiés; moteur de valorisation sectoriel; synchronisation Google Sheets; interface HTML; reverse DCF et dégradation silencieuse alimentés par données fiables.
