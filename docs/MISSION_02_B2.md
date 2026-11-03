# B2 — ingestion hors réseau et réconciliation

Base main 5dd93d6. Dépendance B1, PR #8 au commit
5d551e02bda63842626d0e7a68fe747d5bbccbec. La fonction route_prepared
charge B1 uniquement s'il est installé. En son absence elle renvoie
DEPENDENCY_BLOCKED avec trois indicateurs d'éligibilité/d'appel faux.
La CI teste le mode autonome puis le mode avec cette version épinglée
de B1 dans un répertoire séparé. Ceci établit la compatibilité hors ligne.
L'intégration définitive de B1 puis un retest sont nécessaires.
C1 dépend aussi de l'intégration/retest du collecteur SEC de la PR #4.

## Entrées publiques

schemas/identity-input.schema.json décrit un tableau JSON Draft 2020-12.
tests/fixtures/identity-fictional.json contient exclusivement des identités,
références et assertions fictives. Tous les noms, identifiants et MIC de cette
fixture sont des exemples syntaxiques. Aucun identifiant n'est vérifié auprès
d'un registre réel. Aucune position, quantité détenue ou prix d'achat ne figure
dans ce schéma. Les clés inconnues sont rejetées.

Trois objets distincts identifient l'émetteur, le titre et la cotation. Les clés
internes sont fournies, jamais fabriquées à partir du ticker. ISIN, LEI, CIK et
MIC sont des chaînes au format attendu ou UNKNOWN. Aucune conversion
numérique, aucun padding, changement de casse ni correction implicite.
Le contrôle syntaxique et la preuve fournie sont deux exigences distinctes.
Le checksum d'un identifiant et la vérification du registre restent à la
charge du fournisseur de preuve. UNKNOWN pour ISIN/MIC bloque l'identité.
Pour CIK/LEI, UNKNOWN permet une autre route explicitement documentée.

Chaque preuve contient ref, checked_at avec fuseau et result. La date de rejeu
as_of est un datetime UTC conscient du fuseau fourni par l'appelant. Les dates
sans fuseau, invalides ou futures sont refusées. Le module ne crée aucun timestamp.
Il ne déduit pas une durée de validité d'un contrôle d'accès. Le fournisseur doit
actualiser ses assertions selon la politique de sa source avant un plan réel.

La source distingue reference, official_check, access.result,
access.authorization et access.identity, filing.ref et sa preuve d'identité,
xbrl.taxonomy et sa preuve. Une référence seule, ou le mot AUTHORIZED seul,
n'établit ni caractère officiel, ni disponibilité, ni fait XBRL admissible.
Le module vérifie les types, dates et cohérence des assertions fournies ;
il ne visite aucune URL et ne contrôle aucune autorité réelle.
Les seules assertions suffisamment complètes sont transformées en SourceEvidence
pour B1. Un contrôle absent ou incomplet produit UNVERIFIED. Une interdiction
ou indisponibilité déclarée produit BLOCKED. Un émetteur de source discordant
produit NEEDS_REVIEW. Aucun repli automatique de SEC/ESEF vers SEDAR+ ou l'IR.

Pour l'ADR, issuer_id désigne l'émetteur sous-jacent. underlying fournit une preuve
séparée de ce lien. Un émetteur inconnu ou discordant bloque l'identité.
Le ratio JSON est uniquement un objet explicite
{"decimal":"0.125","identity":{"ref":"exemple","checked_at":"2026-01-01T00:00:00Z","result":"VERIFIED"}}.
La chaîne décimale canonique positive est transformée exactement en Decimal,
sans float intermédiaire ni arrondi. C'est un décodage déclaré par le schéma,
pas une coercition des chaînes/nombres JSON arbitraires. Bare string, int, float,
notation exponentielle, NaN, infini et valeurs non positives ne passent pas
le contrat. Une erreur de ratio retire l'éligibilité du reçu sans empêcher
la collecte des comptes du sous-jacent identifié. Les droits ont leur preuve
distincte. Le schéma définit la forme stricte attendue ; les contrôles sémantiques
de positivité, date, identité, collision et autorisation sont dans l'adaptateur.
Le module applique ses propres contrôles au runtime, sans dépendance jsonschema.

## Appel et sorties

load_json(texte) retourne (objet, erreur éventuelle) et refuse clés JSON dupliquées
et constantes non JSON. plan(objet, as_of=instant) renvoie les décisions ordonnées.
Un tableau est obligatoire. prepare valide un enregistrement, reconcile compare
le lot, et route_prepared construit les Instrument et SourceEvidence typés de B1
puis appelle route. Le ratio est déjà Decimal. Les états de refus ne construisent
aucun plan de collecte. Il n'y a aucun client réseau, aucun import dynamique
de code distant et aucune écriture de fichier à l'exécution.

Prepared conserve les assertions originales dans provenance. Le module ne modifie
pas les entrées. Les résultats d'adaptation ne prétendent pas établir de nouveaux
faits. Les décisions de B1 gardent ses refs ; l'appelant conserve les Prepared
pour archiver aussi les dates et résultats distincts de chaque preuve.
Les refus précoces pour JSON ou structure inexploitable ne promettent pas une
provenance structurée ; l'appelant doit conserver le payload original à part.

Les mêmes clés émetteur/titre/cotation avec attributs discordants placent toutes
les lignes en NEEDS_REVIEW, indépendamment de leur ordre. Un même CIK/LEI affecté
à deux émetteurs, un ISIN affecté à plusieurs titres, un ticker+MIC attribué à
plusieurs cotations et des assertions de source contradictoires sont bloqués.
Le lot n'infère pas de relations de succession ou de dates d'effet historiques.
Un conflit peut nécessiter de séparer des lots datés après revue humaine.
Un doublon exact est admissible ; plan marque collection_required uniquement sur
la première occurrence d'une clé émetteur/source éligible dans le lot, y compris
entre une action et son ADR. Il ne simule pas un téléchargement. La déduplication
entre lots et la fraîcheur d'un cache restent des responsabilités de l'orchestrateur.
Les lignes radiées restent ARCHIVED. ETF/ETN/FUND sont exclus avant lecture de
la source même si celle-ci est malformée.

## Preuves et limites

Commandes locales depuis la racine de cette branche
- python -m unittest discover -s tests -q
- PYTHONPATH=/chemin/vers/B1 python -m unittest discover -s tests -q
- python -m py_compile scripts/ingest_identity.py tests/test_ingest_identity.py
- python -m json.tool schemas/identity-input.schema.json
- git diff --check

48 tests sont découverts. En mode autonome 3 tests d'intégration B1 sont
explicitement ignorés. Avec B1 les 48 réussissent, sans test ignoré.
La CI exécute aussi séparément les 10 tests B1 épinglés et la suite existante
de normalisation/valorisation. La compilation est ciblée sur B2, car le
collecteur SEC de main a toujours une erreur de syntaxe préexistante ligne 96.
Le schéma est contrôlé syntaxiquement ; aucun validateur externe JSON Schema
n'est ajouté. Tests de frontières pour JSON mal typé, incomplet, doublonné,
dates/provenance, collision inverse, source bloquée, ratio et ADR, sans réseau.

Ces résultats ne constituent aucun accès SEC/ESEF réel. Le Lot C attend
la revue du contrat B2 et la validation des dépendances.
