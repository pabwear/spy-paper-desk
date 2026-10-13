"""The desk's machine-learning research engine (paper only).

Pure core (no network, no broker): config, data cleaning, features, labels, splits, models, backtest, risk and
decision. The edges are ml/data.py's providers (market data) and, later, the paper-broker adapter. See docs/ML.md.
"""
