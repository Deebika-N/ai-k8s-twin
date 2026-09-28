import http from 'k6/http';
import { check } from 'k6';
import { sleep } from 'k6';

export const options = {
    vus: 10,
    duration: '60s',

    summaryTrendStats: [
        'avg',
        'min',
        'med',
        'max',
        'p(90)',
        'p(95)',
        'p(99)'
    ],
};

export default function () {

    const response = http.get(
        'http://simulation-payment.simulation-resource.svc.cluster.local'
    );

    check(response, {
        'status is 200': (r) => r.status === 200,
    });

    sleep(1);
}
