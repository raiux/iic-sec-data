# Mission 04 — Intégration B3 → C1 (hors réseau)

Mission publique, sans données privées. Branche dédiée créée depuis main, indépendante des branches PR #10 et #11.

Objectif : intégrer les contrats et portes de qualité B3 (PR #10, SHA 9cc0fa6) avec la normalisation SEC C1 (PR #11, SHA ee6879d), sans régression B1/B2, SEC ni V4. Les deux PR sources restent ouvertes et ne sont pas fusionnées automatiquement.

Critères : intégration explicite des intentions admissibles B3 vers candidats C1, conservation des identités émetteur/instrument et des preuves, dates, unités ISO 4217, raw, SHA256 et pointeurs ; aucune valorisation éligible si preuve ADR ou contrôle comptable non validé ; aucun appel réseau ; aucune sélection implicite de chiffres conflictuels ; PRIVATE par défaut ; tests intégrés sans skip sur un même checkout ; CI B3, C1, SEC et régressions existantes vertes.

Interdit : fusion automatique sur main, collecte réelle non vérifiée, ETF dans collecteurs actions, secrets, données privées, valeurs inventées ou API payantes.
