# IIC 06-C3-EVIDENCE-FOUNDATION-20261104

Fondation hors réseau, dépendante de PR #13 au SHA C2 `85f0eb7`.
Branche `feat/mission-06-c3-evidence-foundation`, PR distincte vers main.
Aucun téléchargement, extraction XBRL, authentification, arbitrage, valorisation,
UI ou déploiement n'est implémenté dans ce lot.

## Revue C2 préalable

Lecture d'AGENTS.md, mission C2, code, quatre fils historiques et commentaires
complémentaires. Les tests C2 (20) et la suite intégrée (148) passent avant C3.
Les reproductions des constats anciens sont couvertes : conflits de pointeurs
hors périmètre ; concept brut pour unités/périodes ; reconstruction des groupes ;
types des assertions et taxonomies ; attribution parent indépendante des conflits.
Aucun défaut résiduel de ces constats n'est reproduit. Les fils GitHub restent
historiquement non résolus : cette vérification technique n'est pas une approbation
indépendante de la PR entière ni une authentification de comptes.

## Contrat et limites de confiance

`schemas/c3-document-package.schema.json` décrit un paquet privé strict :

- octets bruts non vides en base64 canonique (conservation des octets binaires,
  sans décodage texte, modification de fins de ligne ou interprétation XML) ;
- SHA256 déclaré, recalculé indépendamment par le validateur ;
- identité déclarée CIK/accession/nom du document ;
- origine déclarée FIXTURE ou IMPORTED et son locator ;
- date d'acquisition déclarée RFC3339 ; contexte de provenance méthode/référence.

Origine, acquisition et identité restent des assertions du fournisseur. Le locator
est conservé sans accès réseau ; il n'est pas certifié. Un hash correct prouve
seulement la correspondance des octets avec une empreinte déclarée, pas leur
provenance. Même un paquet entièrement réécrit avec son nouveau hash ne devient
pas authentique. Le contenu documentaire et son identité interne ne sont pas
interprétés : une identité cohérente dans les métadonnées ne prouve pas que les
octets correspondent au dépôt revendiqué.

`validate_package(package, expected=assertion_c2)` retourne séparément :
`byte_integrity`, `identity_comparison`, `manifest_sha256`, `origin_authenticity`.
Le paramètre expected est facultatif ; s'il est fourni, il doit être une assertion
C2 valide. Les identités CIK/accession/nom et le locator sont comparés exactement,
ainsi que le hash recalculé au hash du manifeste. Aucun champ fourni n'est traité
comme un reçu authentifié. Statuts : INVALID, CONFLICT, ou
CONSISTENT_UNAUTHENTICATED ; jamais VERIFIED. L'origine et la validation comptable
restent UNCHECKED, PRIVATE et valuation_usable=false demeurent obligatoires.
Les erreurs de schéma ont un diagnostic générique sans réémission du contenu
malformé. Les paquets valides structurellement, même à empreinte incohérente,
sont conservés dans une copie privée pour inspection.

## Raccord C2 minimal

`scripts.sec_document_evidence.validate_bundle(bundle, evidence=assertions,
documents=[{'evidence_index': 0, 'package': paquet}])` retourne une enveloppe C3
contenant le rapport C2 inchangé et les contrôles documentaires séparés.
L'indice explicite désigne une assertion C2 ; aucun document n'est choisi
implicitement, aucune assertion de fait n'est fabriquée à partir des octets.
Les indices invalides et paquets incohérents sont signalés individuellement.
Plusieurs paquets peuvent être examinés pour une assertion, sans sélection.
La correspondance d'un paquet ne modifie aucun statut C2 ; ses conflits restent
visibles. L'absence de paquet n'ajoute aucune preuve. Les checks documentaires
ne doivent pas être interprétés comme une nouvelle validation comptable.

Le sérialiseur réutilise la barrière privée existante ; aucun export public.
CLI locale de contrôle d'un paquet :

```sh
python -m scripts.sec_document_evidence --input /chemin/paquet-prive.json
```

Les fixtures des nouveaux tests sont construites en mémoire, clairement fictives,
avec octets binaires et assertions d'archives fictives héritées des tests C2.
Aucun document SEC réel n'est ajouté au dépôt, aucune fixture existante modifiée.

## Validation, intégration et rollback

```sh
python -m unittest tests.test_sec_document_evidence tests.test_c2_accounting -v
python -m scripts.run_offline_tests
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q scripts tests
python -m json.tool schemas/c3-document-package.schema.json
git diff --check
git diff --exit-code -- data tests/fixtures
```

Exécuter ciblés, suite intégrée sans skip, compilation et contrôle des données sur
HEAD et sa fusion de test avec main actualisé. La CI dédiée C3 reprend la suite
intégrée entière avec réseau interdit et vérifie le schéma ainsi que les données.
Les workflows existants découvrent aussi les nouveaux tests.

Dépendance explicite #13 : cette branche contient le C2 non fusionné. Réviser
le diff C3 contre `85f0eb7` ; le diff vers main comprend aussi cette dépendance.
Après autorisation distincte d'intégrer #13, actualiser la branche C3 contre
main et refaire tous les contrôles avant toute décision de fusion C3. Aucun
ordre de fusion n'est une autorisation de fusion automatique.
Rollback du lot : retirer/revertir le commit C3 seul ; les fichiers ajoutés sont
isolés, les états et schémas C2 ne changent pas, aucune migration de données.

Prochain incrément à cadrer : modèle de confiance et preuve indépendante de
l'acquisition, puis extraction et rapprochement des faits. Rien de cela n'est
prouvé par la présente fondation ni autorisé pour une collecte réelle ici.
