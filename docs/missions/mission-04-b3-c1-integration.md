# Mission 04 — Intégration B3 → C1 (hors réseau)

Mission publique, sans données privées. Branche dédiée créée depuis main, indépendante des branches PR #10 et #11.

Objectif : intégrer les contrats et portes de qualité B3 (PR #10, SHA 9cc0fa6) avec la normalisation SEC C1 (PR #11, SHA ee6879d), sans régression B1/B2, SEC ni V4. Les deux PR sources restent ouvertes et ne sont pas fusionnées automatiquement.

Critères : intégration explicite des intentions admissibles B3 vers candidats C1, conservation des identités émetteur/instrument et des preuves, dates, unités ISO 4217, raw, SHA256 et pointeurs ; aucune valorisation éligible si preuve ADR ou contrôle comptable non validé ; aucun appel réseau ; aucune sélection implicite de chiffres conflictuels ; PRIVATE par défaut ; tests intégrés sans skip sur un même checkout ; CI B3, C1, SEC et régressions existantes vertes.

Interdit : fusion automatique sur main, collecte réelle non vérifiée, ETF dans collecteurs actions, secrets, données privées, valeurs inventées ou API payantes.

## Interface livrée

`scripts.b3_c1_pipeline.build_bundle(identity_payload, sec_snapshot_payload, *, as_of, source_locator)` compose le plan privé `iic.collection-plan.v1` de B3 avec les observations candidates C1. L’association est faite uniquement entre une intention simulée `SEC_COMPANYFACTS` et un CIK vérifié/reconcilié par B2 ; le JSON du cache est transmis textuellement à C1, sans réécriture préalable. Les observations gardent `source_sha256` et `raw_pointer`, et le document conserve `raw_document` exactement.

L’enveloppe `iic.b3-c1-integration.v1` est toujours `PRIVATE`, `OFFLINE_SIMULATION`, `source_verification=NOT_PERFORMED` et `valuation_usable=false`. Elle expose séparément `issuer_collection_eligible`, `instrument_valuation_eligible` (par instrument) et le blocage comptable C1 (`UNCHECKED`). Un ADR dont le reçu est structurellement invalide peut conserver l’intention admissible de collecte émetteur, mais son porte-instrument reste fermée. Même si B1 ouvre la porte instrument, aucun calcul de valorisation n’est autorisé avant validation comptable. Les groupes C1 conflictuels ou répétés restent sans observation sélectionnée ; un conflit ajoute `CONFLICT_UNARBITRATED` aux bloqueurs.

Les ETF/ETN/FUND ne produisent aucune intention et ne déclenchent pas l’analyse du snapshot. Une intention ESEF ou autre non SEC est conservée en `unsupported_intents` avec un statut explicite ; elle n’est pas routée artificiellement vers SEC. Les références/provenances sont des assertions privées de l’appelant et ne constituent pas une vérification en ligne. Il n’y a pas de collecteur, de requête réseau, ni d’export public dans cette interface.

La validation s’effectue avec le schéma local Draft 2020-12 `schemas/b3-c1-integration.schema.json`, au moyen du validateur déjà épinglé dans `requirements-validation.txt`. Le workflow `.github/workflows/test-b3-c1-integration.yml` compile le checkout unique, exécute la suite sans réseau et vérifie l’absence de modification des données/fixtures. Commande locale : `python -m scripts.run_offline_tests` après installation des dépendances épinglées.
