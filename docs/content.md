
# Tropical storm detection

This module gives a framework for the implementation of storm tracking algorithms. Currently two storm tracking algorithms are implemented. Thresholds of the algorithms can easily be adapted and testing new tracking algorithms is straight forward within this framework.

## Data preprocessing

Depending on the detection algorithm, climate variable fields have to be provided in the form of dimarray's.


```python
import dimarray as da
U850=da.read_nc(file_name_of_U)['var33']
V850=da.read_nc(file_name_of_V)['var34']
```
