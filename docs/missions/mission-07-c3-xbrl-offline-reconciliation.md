# Mission 07 · Comparaison XBRL hors réseau

La fonction `scripts.reconcile_sec_xbrl.reconcile(bundle, package, evidence=...)`
prend une enveloppe B3→C1 et un paquet C3. Elle renvoie le rapport C2 intact et
des diagnostics privés distincts. Les fonctions existantes valident les octets,
le SHA et les entrées C2. Le lecteur VINCI traite du XBRL-JSON et ne résout
pas les références XML. Le nouveau lecteur utilise Expat et minidom de Python
sans dépendance supplémentaire. Le sérialiseur existant bloque l'export public.

## Lecture et limites

Le lecteur traite une instance XML XBRL 2.1 à faits simples. Il conserve les
octets originaux dans le paquet, les identifiants et indices des faits, leur
QName développé, le texte numérique exact, nil et decimals, les références
aux contextes et unités. Les unités composées conservent numérateur et
dénominateur. Les dimensions explicites conservent les noms développés et les
dimensions typées un fragment XML. Les octets d'origine font foi pour la
représentation XML et les déclarations de namespaces héritées.
Le texte, les tuples, fractions, Inline XBRL et les extensions de taxonomie
n'entrent pas dans la comparaison numérique. Une racine autre qu'une instance
renvoie UNSUPPORTED_FORMAT. Aucun schemaRef ni autre référence externe n'est
résolu. DTD et déclarations d'entités sont refusées avant la construction DOM.
Limites par instance : 2 Mio décodés, profondeur 64, 20 000 éléments, 64
attributs par élément et 100 000 paires observation/fait examinées.

## Interprétation

L'identité et l'accession du paquet sont des déclarations. La comparaison
requiert en plus le CIK du contexte XML avec le scheme SEC attendu, la période
exacte et l'unité admissible. Le QName XML est conservé intégralement.
C1 n'indique pas la version de taxonomie : les URI reconnues sont rapprochées
par famille US-GAAP, IFRS ou DEI et nom local ; la version de la taxonomie
du dépôt n'est pas confirmée. La comparaison est numérique exacte, sans
arrondi selon decimals. Chaque observation C2 est distinguée par son ID.
Les faits absents, contradictoires ou multiples ont des diagnostics séparés ;
aucune valeur n'est choisie. Un contexte dimensionnel ou une unité composée
est conservé mais ne correspond pas à une observation C1 consolidée simple.
Les dépôts amendés sont distingués par accession et jamais arbitrés.

Octets et métadonnées peuvent être falsifiés ensemble. Même une égalité exacte
donne seulement MATCH_UNAUTHENTICATED. L'origine, la consolidation et
l'attribution parent demeurent UNCHECKED, valuation_usable reste false.
Une acquisition indépendante authentifiable sera nécessaire pour aller plus loin.

## Intégration et retour arrière

Base directe : mission 06 (#14, 5ebceae), dépendant de C2 (#13).
La cible de revue est main ; comparer d'abord le diff avec cette base C3.
Revalider après intégration des dépendances. Le lot ajoute uniquement le
module, ses tests et cette note ; un revert du commit du lot les retire
sans migration de données. Aucun téléchargement ni déploiement.
