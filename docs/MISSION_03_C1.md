# C1 — premier incrément de candidats SEC sur snapshots immuables

Autorisation de démarrage C1 reçue le 3 novembre 2026. Branche dédiée issue de
main@1754a262c3911c7bd39536313bf18ccb8630ef25, avec #4 SEC, #8 B1 et #9 B2
déjà intégrées. B3 #10 reste ouverte au moment du démarrage.
Cet incrément autonome utilise uniquement un snapshot SEC schéma 1.
Le branchement aux intentions B3 et les accès réels attendent la revue des
interfaces et de la PR #10. Aucune fusion de dépendance n'est réalisée ici.

## Contrat et limites sémantiques

scripts/normalize_sec.py lit le snapshot sans le modifier et conserve exactement
le texte UTF-8 dans raw_document, son SHA-256 et un pointeur JSON pour chaque
ligne d'observation. Les décimaux JSON sont lus directement en Decimal puis
normalisés en texte exact, sans float intermédiaire. Le texte brut garde les
lexèmes numériques, les dates, les formulaires, les dimensions et la provenance
originelle. L'ID d'une observation dépend de l'empreinte source et du pointeur ;
il ne remplace pas le numéro d'accession. Un nouveau snapshot fournit des IDs
de source distincts. La liaison de révisions entre snapshots reste à implémenter.

L'entrée accepte le cache SEC du dépôt, pas un CompanyFacts brut téléchargé
directement. Le CIK doit déjà être une chaîne de dix chiffres ; aucune résolution
de titre ou conversion implicite de CIK n'a lieu. Un conteneur corrompu ou un CIK
dupliqué bloque la transformation complète. Une ligne invalide, hors périmètre
ou non mappée est conservée par son pointeur et son statut ; elle ne retire
jamais les lignes valides voisines.

La sortie versionnée iic.sec-candidates.v1 est privée. Elle comprend source,
raw_document, observations, groups, coverage et limitations. Aucune publication
automatique, aucun artefact financier téléchargé vers GitHub. Le contenu de
raw_document peut être sensible s'il provient d'un cache privé. Ce module
n'offre pas d'export public, ni de détecteur universel de secrets. Les tests
utilisent soit des exemples fictifs, soit le snapshot public déjà versionné.

Les noms de métriques portent le suffixe reported. Les comptes SEC us-gaap et
ifrs-full ne sont pas déclarés comptablement équivalents sur la seule base
des tags. Pour chaque candidat, namespace/concept, unité, devise, nature
instant/durée, bornes, accession, filed et form restent explicites.
reported_fy et reported_fp restent des métadonnées de dépôt ; ils ne servent
pas à réétiqueter une période comparative. Les périodes proviennent de start/end.
Un exercice Microsoft décalé est donc conservé avec ses bornes originales.

Les flux candidats exigent 300–380 jours et un formulaire annuel. Les instants
n'ont pas de start. Unités shares et devises syntaxiques sont séparées.
Les dates impossibles/futures, valeurs manquantes, booléens et accessions
incomplètes sont rejetés. Les périodes partielles, dimensions explicites,
unités inadmissibles et formulaires hors périmètre sont OUT_OF_SCOPE.
Les autres concepts restent UNMAPPED dans les observations et intacts dans
le document brut.

La cartographie initiale couvre les revenus rapportés, résultat net rapporté,
CFO rapporté, achats de PPE rapportés, cash, actifs et deux types d'actions
distincts. net_income_reported conserve une portée REQUIRES_TAXONOMY_AND_FILING_REVIEW :
il ne certifie pas une attribution au groupe/parent commune aux deux normes.
Les achats de PPE ne sont pas assimilés à une définition économique complète
des CAPEX. Les concepts de dette sont séparés (borrowings, long-term borrowings,
long-term debt, noncurrent long-term debt) ; aucune addition implicite.

Les groupes regroupent les candidats par émetteur/métrique/période/unité :
valeurs différentes => CONFLICT ; valeurs répétées => REPEATED_VALUE_CANDIDATES.
Toutes les observations et leurs IDs sont gardés. Les /A sont des
amendment_candidates, jamais des supersedes inférés automatiquement.
Aucune sélection de valeur ni moyenne n'est produite.

Les trois niveaux de contrôle restent distincts
- structure CANDIDATE/REJECTED/OUT_OF_SCOPE/UNMAPPED ;
- accounting_validation=UNCHECKED ;
- valuation_usable=false pour toute observation, tout groupe et toute couverture.

La couverture porte sur tous les exercices conservés, pas sur le dernier FY.
Une liste de champs manquants vide signifie seulement que les métriques limitées
du noyau sont observées sur au moins une période. Elle ne prouve ni complétude,
ni cohérence comptable, ni disponibilité d'EBIT/taux d'impôt/D&A/BFR nécessaires
à une valorisation. CFO moins achats de PPE n'est pas calculé comme FCFF.

## Sources et état des preuves

Le snapshot data/sec_facts.json contient Cameco CIK 0001009001 avec ifrs-full/CAD
et Microsoft CIK 0000789019 avec us-gaap/USD, ainsi que DEI pour les actions.
Le SHA-256 est calculé à l'exécution sur le texte exact.
Le fichier est une preuve de contenu local, pas d'authenticité du dépôt SEC.

La documentation officielle des API a été consultée le 3 novembre 2026 :
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
Elle décrit les interfaces de données XBRL. Aucun CompanyFacts pilote,
rapport financier ou dépôt primaire n'a été récupéré/rapproché pendant ce lot.
source.online_verification reste NOT_PERFORMED.
L'accès aux pages documentaires ne valide pas une collecte depuis Actions.

## Commandes et résultats

Depuis la branche C1
```sh
python -m unittest discover -s tests -p 'test_*.py' -q
python -m compileall -q scripts tests
python -m scripts.normalize_sec --input data/sec_facts.json --as-of 2026-11-03 > /tmp/c1-sec-candidates.json
git diff --check
git diff --exit-code -- data
```

95 tests réussis (83 de base + 12 C1), compilation réussie.
Les 246 lignes source sont conservées : 242 CANDIDATE, 4 OUT_OF_SCOPE.
Les quatre lignes sont des durées partielles Microsoft. Cameco manque d'actions
moyennes diluées dans ce snapshot ; les actions en circulation restent distinctes.
Les sorties locales ne sont pas versionnées comme nouveaux faits financiers.

Les tests C1 couvrent les décimaux exacts, la source brute immuable, les pointeurs
résolubles, les durées décalées, les rejets parmi des lignes valides, les conflits,
amendements et répétitions, les unités, la séparation de dette, les données
manquantes et la CLI. Le job C1 exécute la suite complète sur un seul checkout
et vérifie que data n'a pas changé. Il installe les dépendances de validation
si B3 est intégrée ultérieurement ; C1 lui-même utilise la bibliothèque standard.

## Prochaine étape C1

1. Revoir la cartographie et le contrat des candidats, puis intégrer/retester
   B3 selon décision du coordinateur avant le raccordement aux intentions.
2. Vérifier l'accès autorisé et le contact SEC du runner, les dépôts primaires
   Cameco/Microsoft et les tags, périmètres, unités et périodes exacts.
3. Enregistrer des fixtures officielles datées/empreintes et comparer
   les candidats aux états financiers. Définir des liens de révision
   inter-snapshots et une sélection explicite sous contrôle des conflits.
4. Valider séparément les contrôles comptables et les entrées obligatoires
   de chaque méthode avant tout statut utilisable en valorisation.

Ce premier incrément ne termine pas la recette C1 complète. ESEF/VINCI reste
l'étape C2 ; aucune donnée IFRS/US-GAAP manquante n'est inventée.


### Unités monétaires du pilote

La liste hors ligne est extraite du jeu ISO 4217 de pycountry, objet Git
`9f41243f4a15bbc8f4963f32d98ee371e7fe415d`
([source figée](https://api.github.com/repos/pycountry/pycountry/git/blobs/9f41243f4a15bbc8f4963f32d98ee371e7fe415d)).
Le pilote exclut les unités de fonds ou de compte BOV, CHE, CHW, CLF, COU,
MXV, USN, UYI, UYW, XAD, XBA–XBD, XDR, XSU, XUA, les métaux XAG, XAU,
XPD, XPT et les codes spéciaux XTS, XXX. Il vérifie l'appartenance à cette
version, pas la validité historique à la date du dépôt ni la devise déclarée
par l'émetteur. Une unité hors périmètre reste dans le document source.
