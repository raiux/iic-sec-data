# IIC 09-ANNUAL-HISTORY-PERSISTENCE-20261104

## Périmètre et dépendances

Branche `feat/mission-09-annual-history-persistence` depuis
`9f0b106730ac705fb77af5aaf148154abee1d0a9` (mission 08 / PR #18).
PR distincte vers main ; dépendances explicites #13, #14, #15 et #18.
Le diff vers main inclut ces dépendances non fusionnées ; le diff propre du lot
se lit contre `9f0b106`. Les branches dépendantes ne sont pas modifiées.
Mission source : commentaire 6636172227 sur #11. #16 et #17 restent ouverts.

Ce lot fournit seulement une persistance PRIVÉE LOCALE du registre 08 et un import
transactionnel de rapports JSON locaux explicites. Aucun réseau, collecteur de
production, synchronisation programmée, UI/Sheets, recalcul ou admission financière.
PRIVATE et `valuation_usable=false` restent obligatoires ; aucun VERIFIED.

## Audit et reproduction préalable

Les instructions AGENTS.md, mission complète, mission 08 et objectifs #16/#17 ont
été lus. Au démarrage : aucune branche distante mission-09 ni PR équivalente,
aucun processus Python/Git local concurrent identifié. Cela ne constitue pas un
verrou global sur tous les agents externes ; un accusé REÇU est publié sur #11.
Main observé : `6d489db0e847bd305e7aead494436cea5dc24a41`.

Le registre 08 valide/recalcule les rapports, empreintes, observations et contrôles,
conserve versions/conflits et fournit une sérialisation privée. Il est purement en
mémoire. Le collecteur SEC dispose d'un `write_atomic` avec temporaire du même
dossier, flush, fsync et replace, mais sans verrou, révision ou validation de
registre annuel. Le nouveau module reprend cette séquence, en l'étendant avec
exclusion OS, contrôle de révision, relecture du temporaire et diagnostic de commit.
Il ne réutilise pas directement le helper SEC car celui-ci ne permet pas de
distinguer ces phases et n'impose pas le contrat privé ; aucun collecteur modifié.

Premier essai de la suite bloqué localement par jsonschema absent. Installation
des dépendances gratuites épinglées de requirements-validation.txt dans un venv
hors dépôt, puis baseline : **195 tests intégrés OK sans skip**.

La comparaison Python `old_input != new_input` de 08 a été examinée avant toute
correction : les essais `valuation_usable=0/1/true` et
`source.provenance.reference=true/1` sont tous rejetés par le schéma. L'altération
d'une dimension dérivée true vers 1 est déjà détectée par canonical_json dans
validate_history (test 08 existant). **Aucun défaut accessible true/1 n'a été
reproduit sur ces frontières ; aucune correction de 08 n'est donc revendiquée.**
Les nouveaux tests vérifient aussi que les octets source true et 1 créent deux
versions distinctes, et qu'un compteur disque true, false, 1.0 ou "1" est rejeté.
Une première assertion de test mélangeait IFRS/CAD et US-GAAP/USD comme si leurs
concepts étaient identiques : fixture corrigée vers IFRS/CAD, sans changer la règle
métier ni provoquer un faux conflit. Les relances finales suivent cette correction.

## API et format disque v1

`scripts.annual_history_store.HistoryStore` utilise le contrat 08 existant :

- `initialize(history=None)` : création explicite seulement. Refuse tout fichier
  existant, même vide/corrompu. Aucun import ne crée implicitement de registre.
- `load()` : lit et revalide entièrement ; absence, corruption ou format inconnu
  est une erreur. Aucune substitution par un registre vide.
- `import_local(absolute_report_path, expected_revision=token)` : JSON strict puis
  `annual_history.import_report`, le tout pour la mise à jour sous exclusion.
  `import_report(report, ...)` expose la même transaction pour l'API Python.
- `cleanup_orphans()` : suppression explicitement demandée des temporaires du
  registre, sous verrou et seulement si le registre principal est valide. Ne
  choisit jamais un temporaire comme état de récupération.

Le snapshot retourné contient `history`, `generation`, `revision` et
`directory_sync`. Chaque lecture retourne un nouvel objet indépendant du disque.
Le jeton revision est le SHA-256 des octets exacts du fichier. Un appel sans jeton
importe sous verrou sur le dernier état ; avec jeton, il rejette un état obsolète.
Réimport identique : aucun remplacement, compteur/empreinte/octet/première réception
inchangés. Réessayer après commit incertain est donc idempotent.

L'enveloppe JSON a exactement quatre propriétés :
`storage_format="iic.annual-history-store.v1"`, `generation` entier positif strict
(non booléen), `history` au contrat 08, `history_sha256` de la sérialisation privée
canonique. Chaque version source conserve ses octets, SHA, références et conflits.
Le chargement réexécute validate_history et vérifie l'empreinte globale ; il rejette
clés JSON dupliquées, NaN/Infinity, UTF-8 invalide, champs absents/supplémentaires,
versions inconnues, altérations et compteurs de type incorrect. Aucun ancien format
n'est migré implicitement. Limites : 64 Mio par registre et 4 Mio par rapport local,
avec limite 08 de 2 Mio décodés par source. Pas de rotation ni de base distante.
Les empreintes détectent des incohérences locales, pas une falsification cohérente
recalculée par un acteur ayant accès au stockage ; aucune signature/authenticité.

## Confidentialité et usage

Choisir un dossier **dédié**, privé, existant, hors de tout checkout Git, sans alias
symbolique. Les chemins du registre et les entrées locales sont absolus. Le module
refuse le registre sous le dépôt courant ou un autre checkout détectable par .git,
même pour un nom ignoré/non suivi. Tests exclusivement en TemporaryDirectory hors
dépôt. Sur POSIX, le dossier doit être inaccessible au groupe/autres (0700), et les
nouveaux fichiers/verrous sont créés en 0600. Sur Windows, la confidentialité dépend
des ACL du dossier héritées du compte : ce module ne les certifie pas, ne chiffre
pas les fichiers et n'empêche pas une sauvegarde/synchronisation externe du dossier.
L'opérateur doit choisir un emplacement local dont les accès sont maîtrisés.

Exemple Python (chemins choisis par l'opérateur, jamais dans le dépôt) :

```python
from pathlib import Path
from scripts.annual_history_store import HistoryStore
store = HistoryStore(Path(PRIVATE_ABSOLUTE_PATH))
created = store.initialize()  # une fois, explicitement
current = store.load()
result = store.import_local(Path(LOCAL_ANNUAL_REPORT_JSON),
                            expected_revision=current.revision)
```

CLI sans sortie de sources, valeurs, identités ou chemins privés sur stdout :

```sh
python -m scripts.annual_history_store init --store <chemin-absolu-prive>
python -m scripts.annual_history_store status --store <chemin-absolu-prive>
python -m scripts.annual_history_store import --store <chemin-absolu-prive> --report <rapport-local-absolu> --expected-revision <jeton>
python -m scripts.annual_history_store cleanup --store <chemin-absolu-prive>
```

Le rapport local respecte iic.annual-report.v1 de mission 08 (SEC_SNAPSHOT ou
ESEF_JSON fictifs/hors réseau ici). Aucun contenu privé ne doit être versionné,
exporté en public ou envoyé comme artifact CI. Le sérialiseur 08 bloque toujours
l'audience publique. Les résumés CLI exposent seulement compteurs/statut/jeton,
qui restent à utiliser dans un contexte privé.

## Atomicité, concurrence et limites OS

Chaque opération utilise le même fichier de verrou permanent `<registre>.lock` :
verrou exclusif non bloquant flock sur POSIX ou byte-range msvcrt sur Windows.
Lecture, validation, comparaison de révision et remplacement sont sous ce verrou.
Deux processus simultanés ne peuvent pas écraser silencieusement leurs mises à
jour : le second reçoit StoreBusy ou RevisionConflict et doit recharger/réessayer.
Les verrous sont libérés par l'OS après arrêt du processus. Ne jamais supprimer
le fichier de verrou, même lorsqu'aucun processus ne semble actif : un changement
d'inode pourrait séparer l'exclusion. Aucun délai d'attente infini ni retry caché.

Le nouvel état est complètement calculé/validé et sérialisé avant remplacement.
Temporaire privé dans le même dossier/système de fichiers, écriture puis flush et
fsync du fichier ; fermeture, relecture identique et revalidation du temporaire ;
os.replace puis fsync du dossier sur POSIX. Échec avant remplacement : ancien
registre intact. Échec de replace ou de sync après tentative : CommitUncertain,
recharger le chemin principal ; jamais de rollback destructif implicite. Après
remplacement effectif, le nouveau registre est complet même si l'appelant n'a pas
reçu de confirmation. Sur Windows la synchronisation du dossier n'est pas fournie
par cette API : UNAVAILABLE_ON_THIS_OS ; au chargement elle n'est pas attestée.

Les temporaires de l'appel sont nettoyés en finally. Un échec de nettoyage donne
un diagnostic explicite ; un processus tué peut laisser un temporaire privé. Ne
pas l'adopter automatiquement. Le nettoyage explicite est réservé à un dossier
dédié et aux noms `<registre>.*.tmp`. Les sauvegarder pour investigation si besoin.

Garanties limitées aux processus coopérants utilisant le même chemin canonique et
ce protocole, sur un système de fichiers local supportant verrous et replace. Pas
de promesse universelle face à panne d'alimentation, matériel, pilote, NFS/SMB,
répertoire déplacé par un tiers hostile ou écrivain externe ignorant le verrou.
Aucune atomicité multi-fichiers. Une copie de secours est un acte explicite : charger
un snapshot validé puis initialize(snapshot.history) dans un **autre** chemin privé
produit une archive logique indépendante (compteur/jeton ne sont pas identiques).
Pour une copie exacte de l'enveloppe, réaliser une sauvegarde privée cohérente sous
le même verrou. Ne pas supposer qu'une copie copiée ailleurs est authentique.

Si le principal est corrompu : arrêter les écrivains, conserver les octets altérés
et leur diagnostic dans le stockage privé, vérifier séparément une sauvegarde
choisie explicitement avec load(), puis décision humaine de restauration hors de
cette API. Aucun backup n'est sélectionné, écrasé ou restauré automatiquement.

## Tests, intégration et rollback

```sh
python -m unittest tests.test_annual_history tests.test_annual_history_store -v
python -m scripts.run_offline_tests
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q scripts tests
git diff --check
git diff 9f0b106730ac705fb77af5aaf148154abee1d0a9 --exit-code -- data tests/fixtures
```

Tests : roundtrip SEC US-GAAP/IFRS et ESEF, N-1/N/N+1, arrivées hors ordre,
retraitements et conflits sans sélection, archive ancienne non revalidée,
idempotence, JSON/données altérés, versions inconnues, types true/1, erreurs
open/write/flush/fsync/readback/replace/sync dossier/nettoyage, interruption réelle
d'un processus avant écriture/avant replace/après replace, verrou détenu par un
autre processus, deux écrivains simultanés, révision obsolète et reprise sans perte.
Aucune omission ni réseau autorisé par le runner intégré ; les workers installent
aussi le contrôle d'interdiction réseau. Les données/fixtures suivies sont comparées
aux blobs de la base, pas seulement au working tree. Un workflow dédié exerce la
suite intégrée sous Ubuntu et Windows, sans téléversement d'artifact privé.

Résultats finaux et liens CI au SHA livré publiés sur la nouvelle PR et #11 ;
aucune CI déclarée réussie avant son exécution. HEAD et fusion locale isolée avec
main actualisé testés avant push. La fusion de test n'est pas publiée sur main.
Après intégration séparément autorisée des dépendances : actualiser/retester avant
décision d'intégration. Rollback applicatif : retirer le seul commit du lot ; aucune
migration automatique ou suppression de stockage. Conserver les fichiers v1 privés
pour réutilisation par la même version ; une ancienne API en mémoire ne les charge
pas directement. Les données du dépôt et fixtures existantes restent inchangées.

## Restant #16 / #17

#16 : orchestration manuelle/programmée de synchronisation, raccord XML, acquisition
et identité authentifiées, admission aux calculs, vraie sortie valide et reproduction
financière avec moteur/hypothèses, affichage et fraîcheur. La persistance locale ne
prouve ni que les données sont officielles ni qu'une ancienne valorisation est valide.
#17 : configuration Sheets/interface, identité/routage vérifiés, parcours multi-sources
et recette bout en bout. Aucune liste fermée de pilotes ajoutée ; clés d'émetteur 08
inchangées. Aucune de ces issues ne doit être fermée avec ce lot.
