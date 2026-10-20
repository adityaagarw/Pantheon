"""Allocating orders to warehouses (ALLOCATION.md)."""


class ReservationError(Exception):
    pass


class Fulfilment:
    def __init__(self, data_dir, clock):
        self.data_dir = data_dir
        self.clock = clock

    def availability(self, sku):
        raise NotImplementedError

    def allocate(self, order):
        raise NotImplementedError

    def confirm(self, order_id):
        raise NotImplementedError

    def cancel(self, order_id):
        raise NotImplementedError
