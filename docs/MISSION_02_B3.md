# B3 — orchestration intégrée et intentions simulées

## Base et audit de compatibilité

Branche issue de main@1754a262c3911c7bd39536313bf18ccb8630ef25.
Les dépendances sont intégrées par squash dans son historique.

| PR | Commit intégré |
|---|---|
| #4 SEC | 816c54e5dcd9e8de12fc2a31084f66bb3ffab9f0 |
| #7 contrat | 2bdce8562e83065135fe2b61d2f77e6d1b25297c |
| #8 B1 | 328c3a135d1bb31734a62a6d9e99cf7ba06b023f |
| #9 B2 | 1754a262c3911c7bd39536313bf18ccb8630ef25 |

Avant modification, les 83 tests SEC/B1/B2/VINCI/valorisation réussissent
sans omission et compileall réussit sur scripts et tests. L'import
scripts.route_instrument utilisé par B2 résout le module B1 de ce checkout.
Aucune divergence d'interface n'a été observée.

L'ancien workflow B2 effectuait encore un second checkout de B1. Le workflow
intégré le remplace par un seul checkout et fait échouer le job si un test est
ignoré. Les deux autres workflows de tests installent également les dépendances
communes avant leur suite complète. Les workflows de collecte restent séparés.

## Entrée, contrôle et sortie

Installation gratuite et versions explicites dans requirements-validation.txt.
Le validateur jsonschema contrôle les méta-schémas Draft 2020-12 des fichiers
versionnés, puis les instances. rfc3339-validator est installé explicitement
pour activer le contrôle date-time. Sans ce contrôle disponible, la construction
du validateur échoue. Les références de schéma externes sont refusées.
Les contrôles des preuves, des collisions et de l'admissibilité restent
des contrôles sémantiques distincts de la conformité JSON Schema.

Point d'entrée unique de B3
```sh
python -m pip install -r requirements-validation.txt
python -m scripts.offline_pipeline \
  --input tests/fixtures/identity-fictional.json \
  --as-of 2026-11-03T00:00:00Z > /tmp/iic-b3-plan.json
```

build_plan applique le parseur JSON strict B2, reconcile puis route_prepared
avec le B1 intégré. Aucune copie de B1 ou implémentation de route alternative.
Le temps as_of est obligatoire et fourni par l'appelant ; aucune date générée
pendant la construction. La sérialisation canonical_json trie les clés, refuse
les valeurs non JSON et ne passe jamais les Decimal par un float.

Le contrat schemas/collection-plan.schema.json versionne l'enveloppe
iic.collection-plan.v1 avec les champs suivants.

| Champ | Signification |
|---|---|
| visibility, export_policy | PRIVATE et SEPARATE_PUBLIC_PROJECTION_REQUIRED |
| mode, source_verification | OFFLINE_SIMULATION et NOT_PERFORMED |
| as_of | Date UTC normalisée du rejeu |
| status, issues | PLANNED ou REJECTED ; motifs génériques pour JSON inexploitable |
| decisions | Ordre d'entrée, index de ligne, trois clés d'identité, statut, motifs, champs manquants et erreurs structurelles |
| quality | Structure VALID/INVALID ; contrôles comptables NOT_PERFORMED ; sources CALLER_ASSERTIONS_ONLY |
| provenance | Projection des seuls champs de preuve reconnus, rôles distincts, référence, date et résultat déclarés |
| collection_intents | Liste triée par clé émetteur/source, état SIMULATED et index des lignes partageant cette intention |

proposed_adapter conserve une piste admissible, adapter désigne la route acceptée
par les règles hors ligne de B1. Ces deux champs ne certifient pas un accès réel.
Les erreurs JSON Schema exposent chemin et règle, sans recopier de message
contenant des valeurs soumises. La projection de provenance distingue référence
de source, vérification d'origine, résultat et autorisation d'accès, dépôt,
taxonomie et contrôle XBRL. Une date de preuve malformée peut être conservée
comme assertion brute dans la provenance d'une décision rejetée ; elle n'est
pas une date normalisée vérifiée.

Un ratio ADR de forme JSON incorrecte est signalé par structure INVALID, mais
peut conserver la collecte des comptes si l'émetteur et sa source sont admissibles.
B2 retire le ratio invalide et B1 bloque alors la valorisation par reçu.
Une erreur structurelle hors des sous-arbres ratio/droits ADR bloque la ligne en NEEDS_REVIEW.
Les ETF/ETN/FUND reconnus restent EXCLUDED avant toute invocation de route ou
de collecteur, même si leur source est malformée. Les collisions placent toutes
les lignes concernées en revue. Les sources indisponibles, interdites ou
insuffisamment documentées ne produisent aucune intention. Un ADR sans
sous-jacent identifié reste en revue.

Une intention représente uniquement une entrée dans une liste simulée.
Il n'y a ni dispatcher de production ni invocation de collecteur.
Dans un lot, une clé émetteur/source est demandée une seule fois ; les doublons
et reçus rattachés sont associés à cette intention. Les champs de valorisation
ne valent que pour la porte d'entrée du titre. Aucune observation comptable,
valeur financière ou valorisation n'est ajoutée à la sortie.

## Contrat privé et frontière d'export

La sortie de build_plan et de la CLI est **privée par défaut**. Elle contient
visibility=PRIVATE et export_policy=SEPARATE_PUBLIC_PROJECTION_REQUIRED,
imposés par le schéma de sortie. Les références et les IDs peuvent contenir
du texte sensible même si le JSON est structurellement valide. La projection
des champs reconnus est un format d'audit privé ; elle n'est pas une garantie
de publication publique.

canonical_json(plan) conserve les références et identifiants complets pour
l'audit privé. canonical_json(plan, audience='public') lève PublicExportBlocked.
La CLI propose --audience private (défaut) et refuse --audience public avec
un code de sortie non nul, sans JSON sur stdout et sans recopier les valeurs
soumises dans l'erreur. Aucun mécanisme d'export public n'est activé dans B3.
La sérialisation privée et une redirection shell restent capables de produire
un fichier privé : ce contrôle d'API ne peut pas empêcher sa publication
manuelle par un autre logiciel.

Une publication publique nécessite une **étape distincte, implémentée et revue**.
Son contrat devra partir d'un univers public expressément autorisé, résoudre
les clés privées vers des identités publiques approuvées et n'émettre que
des références exactes inscrites dans un registre public vérifié, avec source,
date et décision de revue indépendantes des assertions de l'appelant.
Référence ou ID absent de cette allowlist, preuve manquante ou sélection privée
non autorisée : export bloqué. La provenance complète reste dans le contexte
privé. Cette étape ne consiste ni à remplacer un indicateur PRIVATE par PUBLIC,
ni à masquer du texte avec une regex générique. Aucun algorithme de détection
de secrets universel n'est revendiqué.

Les tests injectent un marqueur sensible fictif dans source.reference,
filing.ref, issuer.identity.ref et les trois niveaux d'IDs. Les entrées restent
conformes au schéma d'entrée. Le marqueur est conservé en sortie privée, tandis
que l'export public API est bloqué et la CLI publique ne restitue aucune valeur.
Les fixtures du dépôt restent fictives et les snapshots restent inchangés.

## Recette exécutée et CI

```sh
python -m unittest discover -s tests -p 'test_*.py' -q
python -m scripts.run_offline_tests
python -m compileall -q scripts tests
git diff --check
git diff --exit-code -- data tests/fixtures
```

106 tests réussissent, sans omission, dont les 83 tests présents à la base.
Les 23 tests B3 couvrent le méta-schéma, des dates impossibles, des références
externes interdites, l'entrée CLI, la répétabilité octet pour octet, les portes
de routage, les collisions, l'ADR, les preuves, la confidentialité et les
contraintes du schéma de sortie. Les tests fonctionnels rendent l'appel SEC
et urlopen interdits. Le runner applique aussi un audit hook aux connexions,
résolutions DNS et envois socket du processus de test. Un sous-processus vérifie
que cet audit hook rejette effectivement une résolution avant son exécution.
L'exécution CLI en sous-processus est testée séparément sur fixture.

Le job IIC integrated offline pipeline compile l'ensemble, exécute toute
la suite avec interdiction réseau et échec sur omissions, puis l'entrée CLI.
Les téléchargements de dépendances se font avant les tests. Un run CI réussi
de cette branche est la preuve après intégration, distincte des anciens runs
de #4, #8 et #9.

## Passage vers Lot C

C attend la revue de ce contrat et une décision explicite de démarrage.
L'intégration des modules et les fixtures ne démontrent pas la disponibilité
réelle des sources. Il reste à contrôler depuis l'environnement cible les
droits et politiques d'accès, l'identité émetteur/titre/classe, les checksums
ISIN/LEI, le dépôt XBRL et la taxonomie SEC, le package ESEF officiel et sa
conversion. Cameco et Microsoft devront être rapprochés des états primaires ;
VINCI devra conserver ses limites comptables et ses avertissements.

La collecte réelle demandera une orchestration distincte autour des intentions,
de la fraîcheur et des reprises, avec conservation atomique des données valides
et de leur provenance. Toute panne ou interdiction gardera son statut explicite.
La normalisation IFRS/US-GAAP, les conversions monétaires et la validation
comptable relèvent des étapes C approuvées. Aucune de ces opérations réelles
n'a été exécutée pendant B3.

## Correctif de revue après bb3829c

Les refs non textuelles ($ref et $dynamicRef avec null, entier, booléen, liste
ou objet) sont rejetées par ValueError avant startswith. Les références
externes restent interdites. Trois tests supplémentaires couvrent ce cas
et la frontière d'export. Les sorties privées conservent leurs assertions
originelles, sans publier de nouveau JSON d'exemple contenant les marqueurs.

## Revue discussion_r4457542565 — termes ADR et RFC 3339

Une date compacte telle que 20261103T000000Z peut passer datetime.fromisoformat
dans B2 tout en échouant au contrôle RFC 3339 de B3. La porte de valorisation
est maintenant forcée à false pour toute structure INVALID. Le schéma de sortie
impose aussi cet invariant, indépendamment de l'implémentation.

Lorsque toutes les erreurs sont limitées à instrument.ratio et/ou
instrument.rights d'un ADR, les termes concernés sont retirés d'une copie
destinée à la réconciliation/routage. L'identité de l'émetteur et sa source
doivent toujours satisfaire les portes B1/B2. La collecte simulée peut donc
rester éligible, avec RECEIPT_SCHEMA_INVALID, tandis que la provenance privée
conserve les termes originaux et que schema_errors expose leurs chemins.
Les autres erreurs continuent de bloquer la ligne ; aucune preuve d'identité
émetteur ou d'accès n'est remplacée.

Les régressions couvrent ratio et droits, date compacte, type de date erroné,
preuves mal typées et champs additionnels sur termes/preuves. Le reçu entièrement
conforme est éligible seulement si les autres portes B1 le permettent. Le test
de l'invariant de sortie refuse une porte de valorisation true avec structure
INVALID. Toute la suite reste hors réseau. Aucun développement C1 n'est inclus.
