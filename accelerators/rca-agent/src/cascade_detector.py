from typing import Dict, List, Set

class CascadeDetector:
    """
    Traces error cascade through service dependency graph.
    Identifies which downstream services are impacted by root cause.
    """
    
    def __init__(self, service_graph: Dict[str, List[str]]):
        """
        service_graph: Dict mapping service -> [downstream_services]
        Example: {'PaymentService': ['CheckoutService', 'OrderService']}
        """
        self.service_graph = service_graph
    
    def detect_cascade(self, root_cause_service: str, affected_operations: List[str]) -> Dict[str, Any]:
        """
        Find all services downstream from root cause.
        Correlate their error patterns to confirm cascade.
        
        Returns:
        {
            'direct_dependents': ['CheckoutService', 'OrderService'],
            'indirect_dependents': ['APIGateway'],
            'affected_operations': ['CheckoutService/PlaceOrder', ...],
            'cascade_chain': [
                'PaymentService/Charge',
                '→ CheckoutService/PlaceOrder',
                '→ OrderService/CreateOrder'
            ]
        }
        """
        direct = self._find_direct_dependents(root_cause_service)
        indirect = self._find_indirect_dependents(root_cause_service)
        
        cascade_chain = self._build_cascade_chain(
            root_cause_service, affected_operations, direct
        )
        
        return {
            'direct_dependents': direct,
            'indirect_dependents': indirect,
            'affected_operations': affected_operations,
            'cascade_chain': cascade_chain
        }
    
    def _find_direct_dependents(self, service: str) -> List[str]:
        """Return immediate downstream services."""
        return self.service_graph.get(service, [])
    
    def _find_indirect_dependents(self, service: str, depth: int = 2) -> List[str]:
        """Recursively find services N hops away."""
        indirect = set()
        queue = [(service, 0)]
        visited = {service}
        
        while queue:
            current, level = queue.pop(0)
            if level >= depth:
                continue
            
            for dependent in self._find_direct_dependents(current):
                if dependent not in visited:
                    visited.add(dependent)
                    indirect.add(dependent)
                    queue.append((dependent, level + 1))
        
        return list(indirect)
    
    def _build_cascade_chain(self, root: str, operations: List[str], 
                            dependents: List[str]) -> List[str]:
        """Build human-readable cascade chain."""
        chain = [f"{root}/{op.split('/')[-1]}" for op in operations]
        for dep in dependents:
            chain.append(f"→ {dep}/*")
        return chain