"""Ueberwachtes Lernen auf Marktdaten -- siehe docs/ML-PLAN.md.

Getrennt von `qt.strategy`, weil hier eine andere Disziplin gilt: Labels
duerfen in die Zukunft schauen, Merkmale nie. Ein Modul, in dem beides
nebeneinander steht, verliert diese Grenze beim ersten Refactor.
"""
