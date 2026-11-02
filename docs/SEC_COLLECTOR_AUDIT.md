# Mission 01 — audit du collecteur SEC (3 novembre 2026)

Référence : issue #3. Base inspectée avant toute modification :
`chore/codex-onboarding-20261103`, commit `5b7ad87`.
Lecture de `AGENTS.md` et `docs/CODEX_FIRST_MISSION.md` effectuée.

## Découverte en lecture seule

- `scripts/` : 10 fichiers Python. Collecte SEC, découverte/collecte ESEF,
  inspection VINCI, deux normalisations, enrichissement, préparation DCF,
  moteurs DCF et BPA/PER.
- `tests/` : 6 modules `test_*.py` au départ, plus `enrich_vinci.py`, copie
  identique de `scripts/enrich_vinci.py`. Doublon conservé ; aucun fichier supprimé.
- `.github/workflows/` : 9 workflows. Collecte SEC quotidienne à 05:20 UTC,
  autres chaînes ESEF/VINCI/valorisation. Le workflow SEC impose Python 3.12,
  un User-Agent via variable GitHub, un intervalle de 1 s et un timeout de job
  de 20 min. Il publie actuellement directement sur main ; configuration
  préexistante inchangée et workflow non déclenché pendant la mission.
- `data/` : 9 JSON : SEC, découverte ESEF, sous-ensemble XBRL VINCI,
  trois sorties de normalisation VINCI, enrichissement VINCI, hypothèses BPA/PER,
  démonstration synthétique. Le rapport XBRL complet utilisé par les workflows
  n'est pas versionné. Les normalisations multiples sont conservées.
- Aucun test SEC existant. Les workflows actuels ne lancent pas de contrôle SEC
  sur PR. Cette mission ne reconfigure pas les workflows.

## Défauts confirmés et plan minimal

| Priorité | Preuve avant correction | Correction |
| --- | --- | --- |
| P0 | `collect_sec.py` finit après un `elif`, ligne 96 ; `py_compile` produit `IndentationError` | Rétablir le chemin exécutable collecte/cache/publication |
| P1 | Aucun code exécutable pour cache, atomicité ou rétention | Cache par CIK, 24 h, écriture atomique, conservation sur erreur |
| P1 | Branche retry 5xx inachevée, pas de protection testée | 3 essais max, timeout 40 s, backoff, arrêt global 403/429 persistant |
| P1 | 4 périodes Microsoft partielles dans le fichier existant | Filtre annuel explicite 300–380 jours, instants sans début, unités et provenance contrôlées |
| P1 | JSON reçu non validé, absence de tests SEC | Vérification CIK/structure/valeurs/dates/accession et tests hors réseau |

Plan retenu : uniquement collecteur, tests et présent rapport. Aucun changement
aux moteurs financiers, aux données publiées, aux concepts configurés ou aux workflows.

## Pilotes : couverture réellement observée dans le dépôt

`data/sec_facts.json` : schéma 1, `generated_at=2026-11-02T21:32:16.618027+00:00`.
Aucun `fetched_at` par société : ce timestamp global ne prouve pas leur fraîcheur.
Sources attendues, non interrogées pendant cet audit :
- Cameco : https://data.sec.gov/api/xbrl/companyfacts/CIK0001009001.json
- Microsoft : https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json

Cameco : 93 observations, IFRS/CAD et actions DEI, fins 2020-12-31 à
2025-12-31, dépôts les plus récents datés 2026-03-19. Présents : Revenue,
ProfitLoss, CFO, capex, cash, Assets, Borrowings, LongtermBorrowings,
actions en circulation. Aucun concept d'actions moyennes diluées configuré
n'est présent. Les deux concepts d'emprunts ne sont pas additionnés.

Microsoft : 153 observations, US-GAAP/USD et actions DEI. Flux annuels
jusqu'au 2026-06-30 (exercice du 2025-07-01 au 2026-06-30), derniers dépôts
2026-07-29. Présents : revenus, résultat net, CFO, capex, cash, actifs,
LongTermDebt, LongTermDebtNoncurrent, actions diluées et actions DEI.
Les premières fins de périodes de revenus/résultat sont 2020-03-31 ;
4 faits de durée partielle sont exclus par le nouveau filtre lors d'une
collecte réussie. Les actions DEI sont des instants de juillet, distincts des
moyennes annuelles. Les deux concepts de dette ne sont pas interchangeables.

Les comparatifs/révisions restent distincts avec `accn`, `filed`, `form`, dates,
valeur, unité et concept. Aucun arbitrage de conflit ni validation comptable
n'est revendiqué. La couverture SEC n'inclut pas ici tous les éléments EBIT,
impôt, D&A, BFR, capitaux propres, passifs, FX nécessaires à une normalisation
commune ou un FCFF vérifié. L'adaptateur de normalisation existant cible ESEF
IFRS/EUR ; cela ne constitue pas une intégration US-GAAP terminée.

## Contrat du correctif

`valid` signifie seulement : CIK correspondant, structure admissible, au moins
un concept financier éligible, valeur numérique finie, accession et dates
cohérentes, forme annuelle, unité compatible et période admissible. Le statut
est toujours `CANDIDATE`, jamais preuve comptable normalisée ni production prête.
Les dimensions explicites sont exclues ; aucune dimension absente n'est inventée.

Schéma 1 et arborescence `facts/namespace/concept/units` conservés. Champs ajoutés
par société : `fetched_at` (uniquement succès), `source_url`, `status`,
`validation_status`, et `error` en cas d'échec. Statuts : `FETCHED`,
`CACHE_FRESH`, `STALE_RETAINED`, ou `UNAVAILABLE` dans la liste homonyme.
Les observations anciennes restent intactes sur échec, sans renouvellement
artificiel de leur date. Un cache historique sans date est périmé, mais réutilisable
comme secours. Toute disparition d'observation précédemment éligible dans une
réponse exige une revue et conserve le cache : choix prudent pouvant bloquer un
rafraîchissement lorsque SEC retire un ancien fait. Un fichier local illisible
fait échouer l'exécution avant publication.

Au moins 1 s entre débuts de requêtes, y compris retries et sociétés ; 403 arrête
les requêtes du lot. 429 respecte Retry-After (secondes ou date HTTP) ; si le délai
excède 120 s, arrêt du lot plutôt que nouvelle requête prématurée. 5xx transitoires
500/502/503/504 et erreurs de transport : essais bornés, backoff. Un téléchargement
malformé/incomplet ne remplace pas les observations précédentes.

Migration : aucune réécriture des JSON versionnés dans cette PR. Les métadonnées
sont ajoutées au prochain succès ; les caches historiques ne sont jamais datés
rétroactivement. Retour arrière : rétablir la version précédente du collecteur
et conserver le dernier JSON, en sachant que le collecteur de base était tronqué.

## Vérifications effectuées

Python 3.12 fourni par l'environnement local (alias `python` Windows indisponible ;
les commandes ci-dessous ont été lancées avec le chemin absolu de cet interpréteur).

- Avant modification : `python -m unittest discover -s tests -p 'test_*.py' -v`
  → **31 tests, OK**. `python -m py_compile scripts/collect_sec.py`
  → **IndentationError, ligne 96**.
- Après modification : même commande unittest → **47 tests, OK**, dont
  **16 tests SEC** ; `py_compile` → succès ; `git diff --check` → succès.
- Tests SEC : réseau interdit via mock, horloge et attente simulées ; intervalle,
  contact/timeout, 403 global, 429 secondes/date/persistant/délai excessif, 5xx,
  transport, cache frais/périmé/sans date/date future, JSON malformé, mauvais CIK,
  rétention des faits et timestamps, historique tronqué, unités/périodes/dimensions,
  conflits/amendements, valeurs invalides, atomicité et compatibilité schéma/pilotes.
- `python scripts/value_earnings.py --assumptions data/vinci_earnings_assumptions_2025.json --enriched data/vinci_enriched_2025.json --output out/vinci_earnings_indicative_2025.json`
  → **CALCULATED_INDICATIVE**.
- `python scripts/value_company.py --input data/demo_synthetic.json --output out/valuation_demo.json`
  → **CALCULATED, production_ready=False**.
- `python scripts/prepare_vinci.py --input data/vinci_enriched_2025.json --output out/vinci_valuation_input.json`, puis
  `python scripts/value_company.py --input out/vinci_valuation_input.json --output out/vinci_valuation_status.json`
  → **INCOMPLETE, production_ready=False**.

Les sorties locales de reproduction ne sont pas publiées comme nouvelles données.
VINCI reste une illustration BPA/PER, pas un DCF ni un objectif de cours validé.
L'écart de capitaux propres documenté dans le dépôt reste non résolu.

## Non vérifié et prochaine étape

Aucun appel SEC réel ; authenticité et exhaustivité des faits existants non
recoupées avec les états officiels. Aucun résultat CI ne doit être déduit des
tests locaux. L'intervalle est garanti pour un processus, pas entre plusieurs
exécutions indépendantes. Filtre de devise syntaxique, sans conversion FX ni
validation de référentiel monétaire. Le choix annuel exclut les exercices courts.

Prochaine étape : revue de cette PR par le coordinateur, décision sur la politique
de publication du workflow et une CI SEC sur PR dans une mission distincte ; puis
normalisation IFRS/US-GAAP et contrôles comptables sourcés. Aucune fusion automatique.
