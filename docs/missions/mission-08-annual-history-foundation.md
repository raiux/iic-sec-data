# IIC 08-ANNUAL-HISTORY-FOUNDATION-20261104

Première fondation privée hors réseau de #16 ; compatibilité d'identité avec #17.
Branche `feat/mission-08-annual-history-foundation`, base mission 07 `33bb946`,
PR distincte vers main, dépendances explicites #13, #14 et #15. Aucune fusion.

## Audit et périmètre

Le collecteur SEC préserve déjà les lignes historiques et met en quarantaine
les observations rejetées. Il retient l'ancien cache après échec et dispose de
`write_atomic`. Ce cache n'est toutefois pas un registre de versions immuables
de rapports/exercices : il agrège les faits par CIK. Les normalisateurs conservent
périodes/provenances et conflits mais ne définissent pas les trois vues annuelles.
Le moteur ESEF existant propose une extraction JSON réutilisable ; son calcul
calendaire n'est pas utilisé pour cette fondation d'exercices décalés.

Ce lot ajoute une API pure en mémoire et un format JSON versionné, pas une
persistance ni une synchronisation. Aucun raccord au collecteur de production,
aucune écriture de cache, migration, UI ou exécution financière. Le mécanisme
atomique existant sera réutilisé et retesté lors d'un futur lot de stockage ;
aucune garantie de reprise disque ou de coordination multi-processus n'est
revendiquée ici. L'échec d'un import laisse l'objet d'entrée intact ; la reprise
consiste à refaire l'import sur cet objet conservé.

## Contrats

`schemas/annual-history.schema.json` définit deux contrats fermés :
`iic.annual-report.v1` pour l'entrée et `iic.annual-history.v1` pour le registre.
Les dates de dépôt, première réception et période comptable sont distinctes.
Les dates et identités sont déclarées, jamais authentifiées.

L'entrée contient :

- `issuer`: scheme CIK + dix chiffres, ou LEI + vingt caractères ; jamais ticker,
  instrument, cours ou position. Aucune liste de sociétés n'est codée en dur.
- `reporting_period`: début/fin inclusifs explicites, durée de 300 à 380 jours
  (même borne annuelle que C1), autorisant exercices décalés et 52/53 semaines.
- `received_at`: horodatage RFC3339 ; `source.filed_at`: date de dépôt déclarée.
- `revision_kind`: INITIAL, RESTATEMENT, AMENDMENT ou UNKNOWN, assertion conservée ;
  `revises`: références explicites à des versions antérieures du même émetteur.
- `source`: document_id stable déclaré, format SEC_SNAPSHOT ou ESEF_JSON,
  locator/provenance déclarés, octets UTF-8 en base64 canonique, SHA256 déclaré.
  L'empreinte est recalculée ; limite 2 Mio décodés par source. Aucun réseau.

Une version conserve l'entrée complète, les observations et contrôles recalculés.
`report_id` identifie émetteur + document_id + hash des octets ; `version_id`
empreinte toutes les métadonnées d'entrée sauf la réception. Un réimport identique,
même reçu plus tard, ne crée ni version ni doublon et garde la première réception.
Des métadonnées divergentes pour le même report_id entraînent un rejet explicite,
sans écrasement. Des octets différents restent des versions distinctes, y compris
pour le même document_id. Les références et empreintes assurent une traçabilité
locale ; elles ne signent ni n'authentifient un registre fourni par un tiers.

Les snapshots SEC schema 1 sont traités par C1 puis C2 existants, sans modifier
leur code ; le CIK déclaré doit être présent. Le registre archive les observations
de cet émetteur seulement, mais conserve les octets complets du document source.
L'horloge C1 est la date de dépôt déclarée pour que la réception n'altère pas
l'interprétation. Les erreurs structurelles imbriquées sont rejetées sans perte.
Le lot ne prétend pas convertir le snapshot CompanyFacts en dépôt authentique.

ESEF_JSON réutilise `normalize_financials.extract_facts` et sa convention de
périodes (fin exclusive du JSON ESEF convertie en date inclusive). Les formats
`matching_facts` et `facts` sont acceptés ; `lei` doit correspondre et les entités
`scheme:<LEI>` sont confrontées sans vérification indépendante. Le QName textuel,
les dimensions, unités, valeurs exactes et pointeurs source sont conservés.
Cette compatibilité JSON ne prétend pas à une ingestion générale de dépôts ESEF.
XML/Inline XBRL et ZIP ne sont pas importés par cette API ; mission 07 reste
séparée et pourra être raccordée dans un lot cadré.

## Vues, conflits et confiance

`issuer_views` ne choisit aucune version. Les périodes sont indexées par dates
comptables exactes et émetteur, sans clé ticker/année civile. Les périodes
comparatives annuelles sont conservées même dans un rapport plus récent.
Les valeurs contradictoires à concept/période/unité/dimensions identiques sont
signalées avec toutes les références. Les conflits C2 demeurent visibles.
Les faits instantanés sont associés à une période annuelle de même fin seulement
si elle est unique dans le rapport ; sinon ils restent conservés et explicitement
non affectés (`AMBIGUOUS_ANNUAL_PERIOD`). Les faits trimestriels/invalides restent
archivés sans devenir automatiquement des observations annuelles.

- `latest_received` : rapport(s) de première réception la plus récente, égalités
  conservées ; statut RECEIVED_UNAUTHENTICATED ou MISSING.
- `latest_available` : période(s) comptable(s) de fin la plus récente contenant
  des observations ; aucune sélection en cas d'égalité. Chaque période porte son
  état MISSING, INCOMPLETE, UNCHECKED ou CONFLICT. Un nouveau rapport vide est
  archivé mais ne remplace pas le dernier exercice contenant des observations.
- `latest_admissible` : toujours bloqué dans le système actuel, sans période.
  Les contrôles sont recalculés depuis les octets, pas acceptés depuis un booléen
  du fournisseur. C2 et l'extraction ESEF n'authentifient aucune source ; il
  n'existe donc aucun chemin d'admission dans ce lot, ni test de promotion fictif.

PRIVATE et valuation_usable=false restent imposés. Aucun VERIFIED ni arrondi,
ratio, valorisation ou arbitrage. Une égalité de hash n'est pas une preuve d'origine.

Un `retained_output` facultatif archive des octets d'une sortie antérieure,
avec émetteur, date, référence et versions d'entrées déclarées. Il est conservé
inchangé lors des imports, sans lui attribuer de validité : la vue le signale
NOT_REVALIDATED et ne l'affiche que pour son émetteur. `last_valid_output` reste
null ; aucune sortie authentifiée valide n'est prétendue disponible actuellement.
La validation métier d'une ancienne valorisation est un travail restant de #16.

## API et validation

```python
from scripts.annual_history import new_history, import_report, issuer_views, serialize
history = new_history()  # ou retained_output=archive_privee
history = import_report(history, rapport_local)
views = issuer_views(history, rapport_local['issuer'])
private_json = serialize(history)
```

`validate_history` recalcule observations, contrôles et IDs et rejette doublons,
altérations de métadonnées/version, faux contrôles et liens de retraitement
absents/étrangers. Le sérialiseur existant bloque tout export public. Le registre
n'intègre pas de configuration de portefeuille ; des instruments distincts
peuvent référencer la même identité d'émetteur sans dupliquer ses comptes.

```sh
python -m unittest tests.test_annual_history -v
python -m scripts.run_offline_tests
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q scripts tests
python -m json.tool schemas/annual-history.schema.json
git diff --check
git diff --exit-code -- data tests/fixtures
```

Ciblés, suite intégrée sans skip, compilation et contrôles de conservation à
exécuter sur HEAD et fusion de test avec main courant avant publication.

## Intégration, rollback et restant #16/#17

Le diff vers main inclut les dépendances non fusionnées ; réviser le diff propre
contre `33bb946`. Après intégration séparément autorisée des dépendances, actualiser
cette branche et refaire tous les contrôles. Rollback : revert du seul commit du
lot, qui ajoute des fichiers isolés ; aucune donnée ni migration à inverser.

#16 reste ouvert : stockage durable/atomicité/reprise disque, synchronisation
manuelle et programmée, raccord aux paquets XML, admission réellement authentifiée,
restauration d'une valorisation valide, références aux versions du moteur et aux
hypothèses pour reproduction financière, affichage de fraîcheur/historique.
#17 reste ouvert : parcours Sheets/interface vers configuration dynamique,
identité/routage vérifiés, orchestration et couverture multi-sources, prise en
charge explicite des sources non supportées et recette de bout en bout. Ici,
seule la fondation d'identité sans liste fermée ni clé d'instrument est testée.
Aucune de ces deux issues n'est close par cette fondation.
