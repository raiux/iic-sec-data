# Mission 05 — C2, validation comptable officielle SEC (Microsoft et Cameco)

Mission publique hors réseau, sans secret ni donnée personnelle. Branche dédiée issue de main. Reprendre par intégration contrôlée les changements validés de PR #12 (B3→C1), sans travailler sur sa branche et sans fusion automatique.

Objectif : rendre les observations normalisées vérifiables par rapprochement aux dépôts SEC officiels (références documentaires, accession, dates, exercices, unités, taxonomy, scope et dimensions), sans inventer ni sélectionner arbitrairement des valeurs. Distinguer données de fixture et données officielles effectivement vérifiées. Microsoft US-GAAP et Cameco IFRS servent de cas d'essai, avec preuve explicite des sources, ou blocage contrôlé si aucune preuve fiable n'est disponible hors réseau.

Critères : vérifier flux consolidés, attribution du résultat à la société mère, cohérence exercice/période et amendements, USD/CAD/shares, conflits et données manquantes ; conserver raw, SHA256, pointeurs, source locator et PRIVATE. Un état VERIFIED ne peut être émis que si toutes les preuves indépendantes sont disponibles et testées ; sinon UNCHECKED et valuation_usable=false. Ne jamais calculer une juste valeur sur des chiffres non vérifiés. Préserver B1/B2/B3/C1 et V4. Tests intégrés et CI sur un checkout unique, sans skip, avec fixtures fictives et cas négatifs. Aucun réseau non autorisé, API payante, ETF collecteur actions, donnée financière inventée, secret ni publication de données privées. Pas de fusion dans main.

## Incrément C2 livré — rapprochement conservateur hors réseau

`scripts.validate_sec_accounting.validate_bundle(b3_c1_bundle, evidence=...)` reçoit l'enveloppe privée B3→C1. Il revalide SHA-256 et pointeurs RFC 6901 contre le texte brut immuable, compare les champs normalisés au fait C1 brut, contrôle la forme d'accession, le formulaire annuel, les dates, périodes/durées, l'exercice déclaré (`fy`/`fp`), les dimensions, unités et cohérence de taxonomie avec l'assertion B3. Toute divergence déterministe est `CONFLICT`; une correspondance de structure n'est pas une validation comptable.

Le manifeste de dépôt facultatif est un JSON strict qui porte CIK, accession, formulaire, date de dépôt, taxonomie, URL d'archive SEC, SHA-256 déclaré et les faits/contextes revendiqués. C2 vérifie la forme de l'URL `www.sec.gov/Archives/edgar/data/...` et confronte ces assertions aux champs C1. Le manifeste ne contient pas les octets du dépôt ni un reçu d'acquisition authentifié : même une correspondance exacte est `ASSERTIONS_MATCH_UNAUTHENTICATED`, jamais `VERIFIED`. La validation en ligne et l'authenticité de l'archive n'ont pas été exécutées dans cette mission ; `source_verification=NOT_PERFORMED` demeure obligatoire.

La sortie garde l'enveloppe `PRIVATE`, le snapshot brut, son hash/pointeurs, les groupes et leur absence de sélection, ainsi que les portes B3 émetteur et instrument. L'absence d'un CIK demandé dans le snapshot crée une ligne C2 explicite sans observation, au lieu de perdre l'intention B3. Les conflits, formulaires amendés et faits dimensionnels restent visibles ; aucun chiffre n'est choisi. `accounting_validation` reste `UNCHECKED` sauf conflit de données détectable, et `valuation_usable` est toujours false. Le schéma Draft 2020-12 interdit le statut `VERIFIED` pour les résultats C2.

Le snapshot local fournit des exemples `ifrs-full` pour le CIK Cameco `0001009001` et `us-gaap` pour le CIK Microsoft `0000789019`. Les tests vérifient ces namespaces ainsi que le cas de résultat net : l'attribution à la société mère et le scope consolidé restent `UNCHECKED` sans contexte officiel d'instance XBRL. Ces vérifications portent sur le snapshot du dépôt, pas sur une consultation actuelle des dépôts officiels. Aucun dépôt primaire Cameco ou Microsoft n'a été téléchargé ou rapproché en ligne ici ; pas de preuve de comptes vérifiés ni de juste valeur.

## Validation locale et CI

```sh
python -m pip install -r requirements-validation.txt
python -m unittest discover -s tests -p 'test_*.py' -q
python -m scripts.run_offline_tests
python -m compileall -q scripts tests
python -m json.tool schemas/c2-accounting-validation.schema.json
git diff --check
git diff --exit-code -- data tests/fixtures
```

Le workflow `.github/workflows/test-c2-accounting.yml` utilise un checkout unique, les dépendances gratuites épinglées, la suite complète sans réseau ni skip, compilation et contrôle des données/fixtures. Les runs C2, SEC, C1 et B3→C1 sont distincts et doivent être verts sur le SHA de PR avant validation.
